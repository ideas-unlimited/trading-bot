#!/usr/bin/env bash
# Update the deployed checkout from git and restart what changed.
#
#   sudo bash /opt/trading-bot/deploy/update.sh
#
# The deployed tree is a plain git clone: this script never edits files, it
# only fast-forwards, reinstalls the package and restarts the running units.
set -euo pipefail
APP_DIR=${APP_DIR:-/opt/trading-bot}

if [[ ${EUID:-1} -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi
if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "$APP_DIR is not a git checkout" >&2
  exit 1
fi

cd "$APP_DIR"
BEFORE=$(git rev-parse --short HEAD)
git fetch --quiet origin
git merge --ff-only --quiet "@{u}"
AFTER=$(git rev-parse --short HEAD)

if [[ "$BEFORE" == "$AFTER" ]]; then
  echo "already at $AFTER"
  exit 0
fi

.venv/bin/pip install -q -e ".[web]"
chown -R trader:trader runs logs 2>/dev/null || true
systemctl restart tradingbot-web

for unit in $(systemctl list-units --type=service --state=running 'tradingbot@*' --no-legend --plain | awk '{print $1}'); do
  systemctl restart "$unit"
done

echo "$BEFORE -> $AFTER"
