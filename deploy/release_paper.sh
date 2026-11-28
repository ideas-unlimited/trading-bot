#!/usr/bin/env bash
# Standard demo release: deploy BOTH halves of a promotion on this box.
#
#   1. the dry-run sleeve - tradingbot-dryrun picks the manifest up on its
#      next cycle automatically (measurement, no orders);
#   2. the trading bot    - tradingbot@<strategy>-demo, demo orders, Bots page.
#
# Usage (on the box):
#   sudo bash deploy/release_demo.sh <strategy-name>
#
# Naming convention: bots are named <strategy>-<env> (capitulation_dip_uni-demo).
# One symbol may carry several strategies and one box hosts both worlds, so a
# short symbol name or a bare strategy name is never enough to be unambiguous.
#
# The env file is generated from the manifest, including the three risk knobs
# (RISK_PER_TRADE / MAX_POSITION_PCT / MAX_DRAWDOWN_STOP) read from the
# manifest evidence.  The script refuses a manifest that does not carry its
# measured risk profile - that omission is how all five bots ended up at
# run_bot.sh defaults on 2026-11-28.
#
# Never prints secret values; HEARTBEAT_URL is copied on the box only.
# Idempotent: re-running a released bot verifies it instead of clobbering.
set -euo pipefail

strategy=${1:?usage: release_demo.sh <strategy-name>}
name=${strategy}-demo

[[ $EUID -eq 0 ]] || { echo "refused: run with sudo (writes /etc/tradingbot)" >&2; exit 1; }
repo=/opt/trading-bot
manifest=$repo/promotions/demo/${strategy}.json
conf=/etc/tradingbot/${name}.env
[[ -r $manifest ]] || { echo "refused: no demo manifest $manifest" >&2; exit 1; }

# --- read the manifest (no secrets in a manifest) ---
eval "$(python3 - "$manifest" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
e = d.get("evidence", {})
keys = ("risk_per_trade", "max_position_pct", "max_drawdown_stop")
missing = [k for k in keys if k not in e]
if missing:
    sys.stderr.write(f"refused: manifest evidence lacks {', '.join(missing)}"
                     " - record the measured risk profile first\n")
    sys.exit(3)
out = {
    "REL_SYMBOL": d["symbols"][0],
    "REL_TF": d["timeframe"],
    "REL_CAPITAL": str(int(d["capital"])),
    "REL_ACTIVE": "yes" if d.get("active", True) else "no",
    "REL_RISK": str(e["risk_per_trade"]),
    "REL_CAP": str(e["max_position_pct"]),
    "REL_DD": str(e["max_drawdown_stop"]),
}
for k, v in out.items():
    print(f"{k}={v}")
PY
)" || { echo "refused: could not read the manifest risk profile" >&2; exit 3; }
[[ $REL_ACTIVE == yes ]] || { echo "refused: manifest $strategy is not active" >&2; exit 1; }
echo "manifest   $strategy  $REL_SYMBOL  $REL_TF  \$$REL_CAPITAL  risk $REL_RISK cap $REL_CAP brake $REL_DD"

# --- generate the env file ---
mkdir -p /etc/tradingbot
want_symbol="$REL_SYMBOL" want_tf="$REL_TF" want_strategy="$strategy" want_cap="$REL_CAPITAL"
want_risk="$REL_RISK" want_cap_pct="$REL_CAP" want_dd="$REL_DD"
if [[ -f $conf ]]; then
  mismatch=$(python3 - "$conf" <<PY
import sys

def norm(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return str(x or "")

want = {
    "SYMBOL": "$want_symbol", "INTERVAL": "$want_tf", "STRATEGY": "$want_strategy",
    "CAPITAL": "$want_cap", "RISK_PER_TRADE": "$want_risk",
    "MAX_POSITION_PCT": "$want_cap_pct", "MAX_DRAWDOWN_STOP": "$want_dd",
}
env = {}
for line in open(sys.argv[1]):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k] = v
bad = [k for k, w in want.items() if k not in env or norm(env[k]) != norm(w) or str(env[k]).strip() == ""]
print("".join(f" {k}({env.get(k, 'missing')}!={want[k]})" for k in bad))
PY
)
  [[ -z $mismatch ]] || {
    echo "refused: $conf exists with different values:$mismatch" >&2
    echo "edit the file or demote first; the script never clobbers a live bot config" >&2
    exit 1
  }
  echo "env        $conf already matches the manifest"
else
  cat > "$conf" <<EOF
SYMBOL=$want_symbol
INTERVAL=$want_tf
STRATEGY=$want_strategy
CAPITAL=$want_cap
STATE=/var/lib/tradingbot/demo/${name}.json
POLL=60
BOT_ENV=demo
RISK_PER_TRADE=$want_risk
MAX_POSITION_PCT=$want_cap_pct
MAX_DRAWDOWN_STOP=$want_dd
EOF
  if [[ -r /etc/tradingbot/dryrun.env ]] && grep -q '^HEARTBEAT_URL=' /etc/tradingbot/dryrun.env; then
    grep '^HEARTBEAT_URL=' /etc/tradingbot/dryrun.env >> "$conf"
  else
    echo "warning: no HEARTBEAT_URL in /etc/tradingbot/dryrun.env; this bot will not heartbeat" >&2
  fi
  chown root:trader "$conf"
  chmod 640 "$conf"
  echo "env        wrote $conf"
fi

# --- dry-run sleeve (informational; --all picks it up itself) ---
fwd=/var/lib/tradingbot/demo/${strategy}/dryrun/status.json
if [[ -f $fwd ]]; then
  echo "dry-run    sleeve replaying ($fwd)"
else
  echo "dry-run    no state yet - the runner creates it on its next cycle (poll 900s)" >&2
fi

# --- start / verify the trading bot ---
systemctl enable --now tradingbot@${name}.service >/dev/null 2>&1 || true
sleep 8
if [[ $(systemctl is-active tradingbot@${name}) != active ]]; then
  echo "FAIL       tradingbot@${name} is not active; check: journalctl -u tradingbot@${name}" >&2
  exit 1
fi
# The unit appends stdout to /var/log/tradingbot/<name>.log (StandardOutput),
# so the journal only carries systemd's own lines: grep the log first and keep
# the journal as the fallback for a unit configured the other way round.
demo_line="on Binance DEMO account"
demo_seen=0
for _ in 1 2 3 4 5 6; do
  if grep -qs "$demo_line" "/var/log/tradingbot/${name}.log" \
     || journalctl -u "tradingbot@${name}" --no-pager 2>/dev/null | grep -q "$demo_line"; then
    demo_seen=1
    break
  fi
  sleep 5
done
if (( ! demo_seen )); then
  echo "FAIL       no demo-account line yet (log /var/log/tradingbot/${name}.log or journal) - the loop may have exited" >&2
  exit 1
fi
sleep 3
if curl -fsS http://127.0.0.1:9000/api/bots 2>/dev/null | python3 -c "
import json, sys
bots = json.load(sys.stdin)['bots']
ok = any('${strategy}' in b['name'] and b.get('last_heartbeat_at') for b in bots)
sys.exit(0 if ok else 1)
"; then
  echo "trading    tradingbot@${name} active, registered, heartbeat fresh"
else
  echo "warn       bot active but not yet on /api/bots with a heartbeat; re-check in a minute" >&2
fi
echo "done       $name ($strategy) released: dry-run sleeve + trading bot"
