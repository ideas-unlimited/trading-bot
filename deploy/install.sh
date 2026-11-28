#!/usr/bin/env bash
# Provision a fresh Debian 13 (or Ubuntu 24.04) box for the trading bot.
#
#   sudo bash deploy/install.sh
#
# Idempotent: safe to re-run after a git pull.  Never overwrites .env.
set -euo pipefail

APP_DIR=/opt/trading-bot
STATE_DIR=/var/lib/tradingbot
CONF_DIR=/etc/tradingbot
SVC_USER=trader
BOTS="btc eth sol"

log() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root: sudo bash deploy/install.sh"
. /etc/os-release
case "${ID:-}" in
  debian|ubuntu) ;;
  *) die "expected Debian or Ubuntu, found '${ID:-unknown}' (see deploy/README.md)" ;;
esac
command -v apt-get >/dev/null || die "apt-get missing"

log "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip git postgresql postgresql-contrib \
  ufw fail2ban chrony rsync ca-certificates

# A drifting clock trades the wrong bar.
timedatectl set-timezone UTC
systemctl enable --now chrony >/dev/null 2>&1 || true

log "kernel + journal limits"
# Swap only for real pressure; keep the inode/dentry cache, which is what
# makes the 28 MB database feel instant.
cat > /etc/sysctl.d/90-tradingbot.conf <<'EOF'
vm.swappiness = 10
vm.vfs_cache_pressure = 50
EOF
sysctl -q --system
install -d -m 0755 /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/90-tradingbot.conf <<'EOF'
[Journal]
SystemMaxUse=500M
SystemMaxFileSize=100M
RuntimeMaxUse=64M
EOF
systemctl restart systemd-journald >/dev/null 2>&1 || true

log "swap"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "service user"
id "$SVC_USER" >/dev/null 2>&1 || \
  useradd --system --create-home --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$STATE_DIR" "$CONF_DIR"
chown "$SVC_USER:$SVC_USER" "$STATE_DIR"
chmod 750 "$STATE_DIR" "$CONF_DIR"

log "code"
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ "$SRC" != "$APP_DIR" ]]; then
  mkdir -p "$APP_DIR"
  rsync -a --delete \
    --exclude .venv --exclude .git --exclude runs --exclude logs --exclude data --exclude .env \
    "$SRC"/ "$APP_DIR"/
fi
mkdir -p "$APP_DIR/runs" "$APP_DIR/logs"
chown -R "$SVC_USER:$SVC_USER" "$APP_DIR/runs" "$APP_DIR/logs"

log "python env"
[[ -x "$APP_DIR/.venv/bin/python" ]] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
(cd "$APP_DIR" && "$APP_DIR/.venv/bin/pip" install -q -e ".[web]")

log "postgres"
systemctl enable --now postgresql >/dev/null
runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='tradingbot'" | grep -q 1 || \
  runuser -u postgres -- psql -qc "CREATE ROLE tradingbot LOGIN"
runuser -u postgres -- psql -q -f "$APP_DIR/deploy/create_databases.sql" >/dev/null

# 8 GB budget.  The database is 28 MB, so it needs almost nothing: 512 MB of
# buffers already holds every page we own, and the rest of RAM is left to the
# OS page cache, where the files (bars) are actually re-read from.
runuser -u postgres -- psql -qtc "ALTER SYSTEM SET shared_buffers = '512MB'" >/dev/null
runuser -u postgres -- psql -qtc "ALTER SYSTEM SET effective_cache_size = '2GB'" >/dev/null
runuser -u postgres -- psql -qtc "ALTER SYSTEM SET work_mem = '16MB'" >/dev/null
runuser -u postgres -- psql -qtc "ALTER SYSTEM SET maintenance_work_mem = '128MB'" >/dev/null
runuser -u postgres -- psql -qtc "ALTER SYSTEM SET max_connections = 30" >/dev/null
runuser -u postgres -- psql -qtc "ALTER SYSTEM SET random_page_cost = 1.1" >/dev/null
# JIT compilation costs more than it saves on millisecond queries over a 28 MB
# database, and holds extra memory while it decides that.
runuser -u postgres -- psql -qtc "ALTER SYSTEM SET jit = off" >/dev/null
systemctl restart postgresql >/dev/null

log ".env"
if [[ -f "$APP_DIR/.env" ]]; then
  echo "keeping existing $APP_DIR/.env"
else
  DB_PASS=$(openssl rand -hex 16)
  runuser -u postgres -- psql -qc "ALTER ROLE tradingbot PASSWORD '$DB_PASS'"
  cat > "$APP_DIR/.env" <<EOF
BINANCE_DEMO_KEY=replace-me
BINANCE_DEMO_SECRET=replace-me
DATABASE_URL=postgresql://tradingbot:$DB_PASS@127.0.0.1:5432/trading_common
EOF
  grep -q '^BINANCE_DEMO_KEY=replace-me' "$APP_DIR/.env" && NEED_KEYS=1 || NEED_KEYS=0
fi
chown "$SVC_USER:$SVC_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

log "bot configs"
write_bot() {
  local name=$1 symbol=$2 tf=$3 strategy=$4 capital=$5
  only_if_missing "$CONF_DIR/$name.env" "SYMBOL=$symbol
INTERVAL=$tf
STRATEGY=$strategy
CAPITAL=$capital
BOT_ENV=demo
STATE=$STATE_DIR/demo/$name.json
POLL=60
# Ping URL from healthchecks.io / Uptime Kuma.  Empty = no heartbeat.
HEARTBEAT_URL="
  chown "root:$SVC_USER" "$CONF_DIR/$name.env"
  chmod 640 "$CONF_DIR/$name.env"
}
only_if_missing() {
  local path=$1 body=$2
  [[ -f "$path" ]] || printf '%s\n' "$body" > "$path"
}
write_bot btc "BTC/USDT" 4h momentum_swing 1000
write_bot eth "ETH/USDT" 4h momentum_swing 1000
write_bot sol "SOL/USDT" 4h momentum_swing 1000

# One loop replays every active demo sleeve; no key is needed (public bars
# only).  The env template documents the heartbeat and alert secrets; it is
# copied only when missing, so a configured host is never overwritten.
for env_name in dryrun alerts watchdog; do
  if [[ ! -f "$CONF_DIR/$env_name.env" ]]; then
    install -m 0640 -o root -g "$SVC_USER" \
      "$APP_DIR/deploy/$env_name.env.example" "$CONF_DIR/$env_name.env"
  fi
  chown "root:$SVC_USER" "$CONF_DIR/$env_name.env"
  chmod 640 "$CONF_DIR/$env_name.env"
done

log "systemd"
install -m 0644 "$APP_DIR/deploy/tradingbot-web.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/deploy/tradingbot-web-live.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/deploy/tradingbot@.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/deploy/tradingbot-dryrun.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/deploy/tradingbot-watchdog.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/deploy/tradingbot-watchdog.timer" /etc/systemd/system/
install -m 0644 "$APP_DIR/deploy/tradingbot-backup.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/deploy/tradingbot-backup.timer" /etc/systemd/system/
chmod +x "$APP_DIR/deploy/run_bot.sh" "$APP_DIR/deploy/backup.sh"
systemctl daemon-reload
systemctl enable --now tradingbot-web.service tradingbot-web-live.service
systemctl enable --now tradingbot-dryrun.service
systemctl enable --now tradingbot-watchdog.timer
systemctl enable --now tradingbot-backup.timer

if [[ "${NEED_KEYS:-0}" == "1" ]]; then
  echo "WARNING: $APP_DIR/.env still has placeholder Binance demo keys."
  echo "         Bots are NOT enabled yet.  Fill it in, then run:"
  for b in $BOTS; do echo "           systemctl enable --now tradingbot@$b"; done
else
  for b in $BOTS; do systemctl enable --now "tradingbot@$b.service"; done
fi

log "firewall"
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null
systemctl enable --now fail2ban >/dev/null 2>&1 || true

log "done"
cat <<EOF
Web terminal : http://127.0.0.1:9000  (via ssh -L 9000:127.0.0.1:9000 <your-ssh-user>@<ip>)
Logs         : journalctl -u tradingbot-web -u 'tradingbot@*' -u tradingbot-dryrun -f
Status       : systemctl status tradingbot@btc
Dry-run      : systemctl status tradingbot-dryrun (state: /var/lib/tradingbot/demo/<strategy>/dryrun/)
Watchdog     : systemctl status tradingbot-watchdog.timer (dead loop is named, 5 min)
Backups      : /var/backups/tradingbot (daily 03:17 UTC, 14 days)
EOF
