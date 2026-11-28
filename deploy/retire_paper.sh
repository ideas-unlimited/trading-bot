#!/usr/bin/env bash
# Retire a released demo bot: the counterpart of release_demo.sh.
#
# Run it after `bot demote <strategy>` once the bot is flat (drained on its
# own signal, or `bot demote --flatten`).  It stops and disables
# tradingbot@<name>, archives the per-bot env file under
# /etc/tradingbot/retired/ and re-checks the box, so reconcile_demo.sh no
# longer expects a manifest for a bot that is gone.
#
# The dry-run sleeve needs no step: `bot dryrun --all` re-reads the manifests
# every cycle and a demoted manifest simply stops being replayed.
#
# Usage (on the box):
#   sudo bash deploy/retire_demo.sh <strategy-name>
#
# Refuses while the bot still holds a position: draining is a decision, and a
# silent market-close is not part of retiring.  Flatten first, then retire.
set -euo pipefail

strategy=${1:?usage: retire_demo.sh <strategy-name> (e.g. jup_liquidity_15m)}
env=${BOT_ENV:-demo}
name="${strategy}-${env}"
conf="/etc/tradingbot/${name}.env"
unit="tradingbot@${name}.service"
stamp=$(date -u +%Y%m%dT%H%M%SZ)

if [[ ! -f $conf ]]; then
  echo "retire     no ${conf}: ${name} is already retired"
  exit 0
fi

# A position left behind would be orphaned: no bot would manage its stop and
# every other bot would treat the balance as its own.  Read the loop's own
# state file rather than the venue, so the answer needs no exchange key.
busy=$(python3 - "$conf" <<'PY'
import json
import sys

state_path = ""
for line in open(sys.argv[1]):
    line = line.strip()
    if line.startswith("STATE="):
        state_path = line.split("=", 1)[1].strip().strip('"')
qty = 0.0
if state_path:
    try:
        qty = float(json.load(open(state_path)).get("qty") or 0.0)
    except Exception:
        qty = 0.0
print(1 if qty > 0 else 0)
print(f"{qty:g}", file=sys.stderr)
PY
)
if [[ $busy == 1 ]]; then
  echo "refused    ${name} still holds a position; flatten it first" >&2
  echo "           POST /api/bots/<id>/halt (or bot demote --flatten), then re-run" >&2
  exit 2
fi

systemctl disable --now "$unit" >/dev/null 2>&1 || true
install -d -m 750 /etc/tradingbot/retired
mv "$conf" "/etc/tradingbot/retired/${name}.env.${stamp}"
echo "retired    ${name}: unit disabled, env archived to /etc/tradingbot/retired/"
echo "next       sudo bash deploy/reconcile_demo.sh   (expect all checks PASS)"
