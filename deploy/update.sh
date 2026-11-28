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

# One-time rename from paper/prod.  Doing it automatically would stop live
# loops inside an update, so refuse and let the operator run the migration
# explicitly, then re-run this script.
legacy=0
for unit in tradingbot-forward.service tradingbot-web-prod.service; do
  systemctl cat "$unit" >/dev/null 2>&1 && legacy=1
done
for path in /var/lib/tradingbot/paper /var/lib/tradingbot/prod /etc/tradingbot/forward.env; do
  [[ -e "$path" ]] && legacy=1
done
for f in /etc/tradingbot/*-paper.env /etc/tradingbot/*-prod.env; do
  [[ -e "$f" ]] && legacy=1
done
if [[ $legacy -eq 1 ]]; then
  cat >&2 <<'MSG'
legacy paper/prod deployment detected.
Run this once, then re-run update.sh:
  sudo bash /opt/trading-bot/deploy/migrate_terms.sh
MSG
  exit 1
fi

# Units and their env files are part of the deployment, not of a code change:
# install them even when the checkout is already current, so an existing box
# can be repaired with the same routine update.
if [[ -f deploy/tradingbot@.service ]]; then
  install -m 0644 deploy/tradingbot@.service /etc/systemd/system/
fi
for unit in tradingbot-dryrun.service tradingbot-web.service tradingbot-web-live.service; do
  if [[ -f "deploy/$unit" ]]; then
    install -m 0644 "deploy/$unit" /etc/systemd/system/
  fi
done
for unit in tradingbot-watchdog.service tradingbot-watchdog.timer; do
  if [[ -f "deploy/$unit" ]]; then
    install -m 0644 "deploy/$unit" /etc/systemd/system/
  fi
done
for env_name in dryrun alerts watchdog; do
  if [[ ! -f "/etc/tradingbot/$env_name.env" ]]; then
    install -m 0640 -o root -g trader \
      "deploy/$env_name.env.example" "/etc/tradingbot/$env_name.env"
  fi
done
systemctl daemon-reload
if [[ -f /etc/systemd/system/tradingbot-dryrun.service ]]; then
  systemctl enable tradingbot-dryrun.service
  systemctl is-active --quiet tradingbot-dryrun || systemctl start tradingbot-dryrun
fi
if [[ -f /etc/systemd/system/tradingbot-watchdog.timer ]]; then
  systemctl enable --now tradingbot-watchdog.timer
fi
# Git on Windows stores every file 0644; units exec these scripts directly.
chmod +x "$APP_DIR"/deploy/*.sh

if [[ "$BEFORE" == "$AFTER" ]]; then
  echo "already at $AFTER"
  exit 0
fi

.venv/bin/pip install -q -e ".[web]"
chown -R trader:trader runs logs 2>/dev/null || true

for web_unit in tradingbot-web tradingbot-web-live; do
  if systemctl cat "$web_unit" >/dev/null 2>&1; then
    systemctl restart "$web_unit"
  fi
done

for unit in $(systemctl list-units --type=service --state=running 'tradingbot@*' --no-legend --plain | awk '{print $1}'); do
  systemctl restart "$unit"
done

# One process for every sleeve; restart it so the new code is what runs.
systemctl restart tradingbot-dryrun

echo "$BEFORE -> $AFTER"
