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

# New units ship in the repo; install them here so a plain update is enough.
install -m 0644 deploy/tradingbot-forward.service /etc/systemd/system/
if [[ ! -f /etc/tradingbot/forward.env ]]; then
  printf '%s\n' 'BOT_ENV=paper' \
    '# Ping URL from healthchecks.io / Uptime Kuma.  Empty = no heartbeat.' \
    'HEARTBEAT_URL=' > /etc/tradingbot/forward.env
  chown root:trader /etc/tradingbot/forward.env
  chmod 640 /etc/tradingbot/forward.env
fi
systemctl daemon-reload
systemctl enable tradingbot-forward.service

for web_unit in tradingbot-web tradingbot-web-prod; do
  if systemctl cat "$web_unit" >/dev/null 2>&1; then
    systemctl restart "$web_unit"
  fi
done

for unit in $(systemctl list-units --type=service --state=running 'tradingbot@*' --no-legend --plain | awk '{print $1}'); do
  systemctl restart "$unit"
done

# One process for every sleeve; restart it so the new code is what runs.
systemctl restart tradingbot-forward

echo "$BEFORE -> $AFTER"
