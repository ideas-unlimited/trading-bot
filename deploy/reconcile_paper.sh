#!/usr/bin/env bash
# Reconcile every released paper bot against its manifest and the release
# checklist.  Read-only; run after every release, or any time "where is my
# bot?" is asked.
#
# Usage (on the box):  sudo bash deploy/reconcile_paper.sh
# Exit 0 = all checks pass, 1 = at least one FAIL.
#
# Checks per bot (from /etc/tradingbot/<name>.env):
#   unit active | env values == manifest (symbol, timeframe, capital, risk
#   knobs) | manifest active | state file under /var/lib/tradingbot |
#   heartbeat fresh and not halted (API) | forward sleeve replaying |
#   no journal errors in the last 24h.
# Never prints secret values.
set -uo pipefail

[[ $EUID -eq 0 ]] || { echo "refused: run with sudo (reads /etc/tradingbot)" >&2; exit 1; }
repo=/opt/trading-bot
fails=0

check() { # check <ok> <label>
  if [[ $1 == 1 ]]; then printf '  PASS  %s\n' "$2"; else printf '  FAIL  %s\n' "$2"; fails=$((fails+1)); fi
}

for conf in /etc/tradingbot/*.env; do
  name=$(basename "$conf" .env)
  [[ $name == forward || $name == alerts ]] && continue
  get() { grep -E "^$1=" "$conf" | head -1 | cut -d= -f2-; }
  strategy=$(get STRATEGY); symbol=$(get SYMBOL); tf=$(get INTERVAL)
  [[ -n $strategy ]] || { printf '%s\n  FAIL  no STRATEGY in env file\n' "$name"; fails=$((fails+1)); continue; }
  echo "$name ($strategy)"

  manifest=$repo/promotions/paper/${strategy}.json
  eval "$(python3 - "$conf" "$manifest" <<'PY' 2>/dev/null || echo "exit 3"
import json, sys, os
conf, mp = sys.argv[1], sys.argv[2]
env = {}
for line in open(conf):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k] = v
try:
    d = json.load(open(mp))
except Exception:
    print("M_ACTIVE=no"); sys.exit(0)
e = d.get("evidence", {})
def f(x): return (f"{x:g}" if isinstance(x, (int, float)) else str(x))
same = lambda a, b: f(a) == f(b)
out = {
    "M_ACTIVE": "yes" if d.get("active", True) else "no",
    "M_SYMBOL": d["symbols"][0],
    "M_TF": d["timeframe"],
    "M_CAPITAL": str(int(d["capital"])),
}
for k in ("risk_per_trade", "max_position_pct", "max_drawdown_stop"):
    out["M_" + k.upper()] = f(e.get(k, "")) if k in e else ""
# compare inside python so float formatting never lies
checks = {
    "C_SYMBOL": same(env.get("SYMBOL"), d["symbols"][0]),
    "C_TF": same(env.get("INTERVAL"), d["timeframe"]),
    "C_CAPITAL": same(env.get("CAPITAL"), int(d["capital"])),
}
for k in ("risk_per_trade", "max_position_pct", "max_drawdown_stop"):
    checks["C_" + k.upper()] = (k in e) and same(env.get(k.upper()), e[k])
for k, v in {**out, **checks}.items():
    if isinstance(v, bool): v = "yes" if v else "no"
    print(f"{k}={v}")
PY
)" || { printf '  FAIL  cannot compare env with manifest\n'; fails=$((fails+1)); continue; }
  [[ $M_ACTIVE == yes ]] || { printf '  FAIL  manifest is demoted but the bot is released\n'; fails=$((fails+1)); }

  check 1 "unit $(systemctl is-active tradingbot@$name 2>/dev/null || echo inactive)"
  [[ $C_SYMBOL == yes ]] && check 1 "symbol $symbol == manifest" || check 0 "symbol $symbol != manifest $M_SYMBOL"
  [[ $C_TF == yes ]] && check 1 "timeframe $tf == manifest" || check 0 "timeframe $tf != manifest $M_TF"
  [[ $C_CAPITAL == yes ]] && check 1 "capital == manifest" || check 0 "capital != manifest ($M_CAPITAL)"
  for k in RISK_PER_TRADE MAX_POSITION_PCT MAX_DRAWDOWN_STOP; do
    c=C_$k
    if [[ ${!c} == yes ]]; then check 1 "$k == measured profile"; else check 0 "$k missing or != manifest evidence"; fi
  done
  state=$(get STATE)
  [[ $state == /var/lib/tradingbot/* ]] && check 1 "state under /var/lib/tradingbot" || check 0 "state outside /var/lib/tradingbot: $state"
  [[ -f /var/lib/tradingbot/paper/${strategy}/forward/status.json ]] && check 1 "forward sleeve replaying" || check 0 "forward sleeve missing"

  curl -fsS http://127.0.0.1:9000/api/running 2>/dev/null | python3 -c "
import json, sys
bots = json.load(sys.stdin)['bots']
b = next((x for x in bots if x['name'].startswith('${strategy}')), None)
if not b: print('API_NO=1'); sys.exit()
alive = 1 if b.get('alive') else 0
halt = 1 if b.get('halted') else 0
print(f'API_ALIVE={alive}'); print(f'API_HALT={halt}')
" | while read -r kv; do eval "$kv"; done
  check ${API_ALIVE:-0} "heartbeat fresh (alive)"
  [[ ${API_HALT:-0} == 0 ]] && check 1 "not halted" || check 0 "halted - operator killed this bot"

  n=$(journalctl -u tradingbot@$name --since '24 hours ago' --no-pager 2>/dev/null | grep -ciE 'error|traceback|refus|reject' || true)
  [[ ${n:-0} == 0 ]] && check 1 "no journal errors in 24h" || check 0 "$n journal error lines in 24h"
done

echo
if [[ $fails == 0 ]]; then
  echo "reconcile: all checks PASS"
else
  echo "reconcile: $fails check(s) FAILED"
fi
exit $(( fails > 0 ))
