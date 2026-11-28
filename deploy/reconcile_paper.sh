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
  strategy=$(get STRATEGY)
  [[ -n $strategy ]] || { printf '%s\n' "$name"; check 0 "no STRATEGY in env file"; continue; }
  echo "$name ($strategy)"

  manifest=$repo/promotions/paper/${strategy}.json
  if [[ ! -r $manifest ]]; then
    check 0 "no paper manifest for $strategy - released without a promotion"
    check 1 "unit $(systemctl is-active tradingbot@$name)"
    continue
  fi

  eval "$(python3 - "$conf" "$manifest" <<'PY'
import json, sys

def norm(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return str(x or "")

same = lambda a, b: norm(a) == norm(b) and bool(str(a or "").strip()) == bool(str(b or "").strip())
env = {}
for line in open(sys.argv[1]):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k] = v
d = json.load(open(sys.argv[2]))
e = d.get("evidence", {})
print("M_ACTIVE=" + ("yes" if d.get("active", True) else "no"))
for k in ("SYMBOL", "INTERVAL", "CAPITAL", "RISK_PER_TRADE", "MAX_POSITION_PCT", "MAX_DRAWDOWN_STOP"):
    mkey = {"SYMBOL": "symbols", "INTERVAL": "timeframe", "CAPITAL": "capital"}.get(k)
    if mkey:
        want = d["symbols"][0] if k == "SYMBOL" else d[mkey]
        if k == "CAPITAL":
            want = int(want)
        key = "C_" + k
    else:
        want = e.get(k.lower())
        key = "C_" + k
    have = env.get(k)
    ok = 1 if (want is not None and have is not None and same(have, want)) else 0
    print(f"{key}={ok}")
    print(f"W_{k}={want if want is not None else ''}")
PY
)" || { check 0 "cannot compare env with manifest"; continue; }

  [[ $M_ACTIVE == yes ]] && check 1 "manifest active" || check 0 "manifest demoted but bot released"
  unit=$(systemctl is-active tradingbot@$name 2>/dev/null)
  [[ $unit == active ]] && check 1 "unit active" || check 0 "unit $unit"

  [[ $C_SYMBOL == 1 ]] && check 1 "symbol == manifest" || check 0 "symbol != manifest (manifest wants $W_SYMBOL)"
  [[ $C_INTERVAL == 1 ]] && check 1 "timeframe == manifest" || check 0 "timeframe != manifest (manifest wants $W_INTERVAL)"
  [[ $C_CAPITAL == 1 ]] && check 1 "capital == manifest" || check 0 "capital != manifest (manifest wants $W_CAPITAL)"
  [[ $C_RISK_PER_TRADE == 1 ]] && check 1 "RISK_PER_TRADE == measured profile" || check 0 "RISK_PER_TRADE missing or != measured ($W_RISK_PER_TRADE)"
  [[ $C_MAX_POSITION_PCT == 1 ]] && check 1 "MAX_POSITION_PCT == measured profile" || check 0 "MAX_POSITION_PCT missing or != measured ($W_MAX_POSITION_PCT)"
  [[ $C_MAX_DRAWDOWN_STOP == 1 ]] && check 1 "MAX_DRAWDOWN_STOP == measured profile" || check 0 "MAX_DRAWDOWN_STOP missing or != measured ($W_MAX_DRAWDOWN_STOP)"

  state=$(get STATE)
  [[ $state == /var/lib/tradingbot/* ]] && check 1 "state under /var/lib/tradingbot" || check 0 "state outside /var/lib/tradingbot: $state"
  [[ -f /var/lib/tradingbot/paper/${strategy}/forward/status.json ]] && check 1 "forward sleeve replaying" || check 0 "forward sleeve missing"

  api=$(curl -fsS --max-time 5 http://127.0.0.1:9000/api/running 2>/dev/null | python3 -c "
import json, sys
try:
    bots = json.load(sys.stdin)['bots']
except Exception:
    print('API_ALIVE=0'); print('API_HALT='); sys.exit()
b = next((x for x in bots if '${strategy}' in x['name']), None)
if not b:
    print('API_ALIVE=0'); print('API_HALT='); sys.exit()
print('API_ALIVE=' + ('1' if b.get('alive') else '0'))
print('API_HALT=' + ('1' if b.get('halted') else '0'))
" 2>/dev/null || true)
  eval "$api"
  [[ $API_ALIVE == 1 ]] && check 1 "heartbeat fresh (alive)" || check 0 "no fresh heartbeat on the paper API"
  if [[ $API_HALT == 1 ]]; then check 0 "halted - operator killed this bot"
  elif [[ $API_HALT == 0 ]]; then check 1 "not halted"
  else check 0 "halt state unknown (API unreachable or bot not registered)"; fi

  n=$(journalctl -u tradingbot@$name --since '24 hours ago' --no-pager 2>/dev/null | grep -ciE 'error|traceback|refus|reject')
  [[ ${n:-0} == 0 ]] && check 1 "no journal errors in 24h" || check 0 "$n journal error lines in 24h"
done

echo
if [[ $fails == 0 ]]; then
  echo "reconcile: all checks PASS"
else
  echo "reconcile: $fails check(s) FAILED"
fi
exit $(( fails > 0 ))
