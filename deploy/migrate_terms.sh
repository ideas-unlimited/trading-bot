#!/usr/bin/env bash
# One-time terminology migration: paper -> demo, prod -> live.
#
#   sudo bash /opt/trading-bot/deploy/migrate_terms.sh
#
# Run this once on a box installed before 2026-11-29, after `git pull` and
# before `deploy/update.sh`.  It stops the old units, renames the databases
# and their tables, moves state directories and env files, and renames
# `tradingbot@<name>-paper|-prod` instances.  Safe to re-run.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/trading-bot}
STATE_DIR=${STATE_DIR:-/var/lib/tradingbot}
CONF_DIR=${CONF_DIR:-/etc/tradingbot}

log() { printf '\n== %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[[ ${EUID:-1} -eq 0 ]] || die 'run with sudo'
[[ -d "$APP_DIR/.git" ]] || die "$APP_DIR is not a git checkout"
command -v psql >/dev/null || die 'psql missing'

export PAGER=cat
psql_run() { runuser -u postgres -- psql -v ON_ERROR_STOP=1 "$@"; }
db_exists() {
  [[ $(psql_run -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='$1'") == 1 ]]
}

rename_db() {
  local old=$1 new=$2
  if db_exists "$new"; then
    if db_exists "$old"; then
      die "both $old and $new exist; merge or drop one by hand, then re-run"
    fi
    log "database $new already present"
    return 0
  fi
  db_exists "$old" || return 0
  log "database $old -> $new"
  psql_run -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$old' AND pid <> pg_backend_pid()" >/dev/null
  psql_run -d postgres -c "ALTER DATABASE \"$old\" RENAME TO \"$new\"" >/dev/null
}

migrate_table() {
  local db=$1
  local has_old has_new
  has_old=$(psql_run -d "$db" -Atc "SELECT to_regclass('public.paper_trades') IS NOT NULL")
  has_new=$(psql_run -d "$db" -Atc "SELECT to_regclass('public.demo_trades') IS NOT NULL")
  [[ $has_old == t ]] || return 0
  log "$db: paper_trades -> demo_trades"
  if [[ $has_new == t ]]; then
    psql_run -d "$db" -c "INSERT INTO demo_trades (order_id, symbol, side, qty, price, filled_at, bot_name, synced_at) SELECT order_id, symbol, side, qty, price, filled_at, bot_name, synced_at FROM paper_trades ON CONFLICT (order_id) DO NOTHING" >/dev/null
    psql_run -d "$db" -c 'DROP TABLE paper_trades' >/dev/null
  else
    psql_run -d "$db" -c 'ALTER TABLE paper_trades RENAME TO demo_trades' >/dev/null
  fi
}

migrate_env_values() {
  local db=$1
  [[ $(psql_run -d "$db" -Atc "SELECT to_regclass('public.bots') IS NOT NULL") == t ]] || return 0
  psql_run -d "$db" -c "UPDATE bots SET env='demo' WHERE env='paper'; UPDATE bots SET env='live' WHERE env='prod'; UPDATE bots SET mode='demo' WHERE mode='paper'; UPDATE bots SET mode='live' WHERE mode='prod'" >/dev/null
  if [[ $(psql_run -d "$db" -Atc "SELECT to_regclass('public.promotions') IS NOT NULL") == t ]]; then
    psql_run -d "$db" -c "UPDATE promotions SET env='demo' WHERE env='paper'; UPDATE promotions SET env='live' WHERE env='prod'" >/dev/null
  fi
}

merge_state() {
  local old=$1 new=$2
  [[ -d "$old" ]] || return 0
  if [[ -d "$new" ]]; then
    log "both $old and $new exist; copying missing files into $new"
    cp -a --no-clobber "$old"/. "$new"/ 2>/dev/null || true
    mv "$old" "${old}.migrated-$(date +%Y%m%d%H%M%S)"
  else
    log "state $old -> $new"
    mv "$old" "$new"
  fi
}

rename_file() {
  local old=$1 new=$2
  [[ -e "$old" ]] || return 0
  [[ -e "$new" ]] && die "both $old and $new exist; resolve by hand"
  log "file $old -> $new"
  mv "$old" "$new"
}

declare -A WAS_ACTIVE=()
declare -a LEGACY_UNITS=()

log 'stopping services that hold the old databases'
for unit in tradingbot-forward.service tradingbot-web-prod.service; do
  if systemctl cat "$unit" >/dev/null 2>&1; then
    systemctl disable --now "$unit" 2>/dev/null || true
  fi
done
for unit in tradingbot-web.service tradingbot-web-live.service tradingbot-dryrun.service; do
  systemctl stop "$unit" 2>/dev/null || true
done
systemctl stop tradingbot-watchdog.timer 2>/dev/null || true
while read -r unit _; do
  case "$unit" in
    *-paper.service|*-prod.service)
      LEGACY_UNITS+=("$unit")
      WAS_ACTIVE["$unit"]=$(systemctl is-active "$unit" 2>/dev/null || true)
      systemctl disable --now "$unit" 2>/dev/null || true
      ;;
  esac
done < <(systemctl list-units --type=service --all --no-legend --plain 'tradingbot@*' 2>/dev/null || true)

log 'databases'
rename_db trading_paper trading_demo
rename_db trading_prod trading_live
for db in trading_demo trading_live; do
  if db_exists "$db"; then
    migrate_table "$db"
    migrate_env_values "$db"
  fi
done

log 'state directories'
merge_state "$STATE_DIR/paper" "$STATE_DIR/demo"
merge_state "$STATE_DIR/prod" "$STATE_DIR/live"
merge_state "$APP_DIR/runs/paper" "$APP_DIR/runs/demo"
merge_state "$APP_DIR/runs/prod" "$APP_DIR/runs/live"

# The dry-run record moved from <sleeve>/forward/ to <sleeve>/dryrun/.
for root in "$STATE_DIR/demo" "$STATE_DIR/live" "$APP_DIR/runs/demo" "$APP_DIR/runs/live"; do
  for f in "$root"/*/forward; do
    [[ -d "$f" ]] || continue
    d=${f%/forward}/dryrun
    if [[ -e "$d" ]]; then
      # A fresh dry-run has already written the new path while the old
      # service left its record behind.  Keep the old one for comparison and
      # let the new path stand, instead of making the operator delete data by
      # hand before the migration can finish.
      archive="$f.pre-dryrun-$(date +%Y%m%d%H%M%S)"
      log "both $f and $d exist; archiving $f as $archive"
      mv "$f" "$archive"
      continue
    fi
    log "state $f -> $d"
    mv "$f" "$d"
  done
done

log 'env files'
rename_file "$APP_DIR/.env.paper" "$APP_DIR/.env.demo"
rename_file "$APP_DIR/.env.prod" "$APP_DIR/.env.live"
rename_file "$CONF_DIR/forward.env" "$CONF_DIR/dryrun.env"

# Older installs put the demo pair in the shared `.env`.  Move it to
# `.env.demo` so live can never load a demo key, then drop it from `.env`.
split_demo_keys() {
  local shared=$APP_DIR/.env demo=$APP_DIR/.env.demo key line
  [[ -f "$shared" ]] || return 0
  grep -qE '^BINANCE_DEMO_(KEY|SECRET)=' "$shared" || return 0
  log "moving demo credentials from $shared to $demo"
  [[ -e "$demo" ]] || install -m 600 /dev/null "$demo"
  for key in BINANCE_DEMO_KEY BINANCE_DEMO_SECRET; do
    line=$(grep -E "^$key=" "$shared" | tail -n 1 || true)
    [[ -n "$line" ]] || continue
    if grep -qE "^$key=replace-me$" "$demo"; then
      sed -i "/^$key=replace-me$/d" "$demo"
      printf '%s\n' "$line" >> "$demo"
    elif ! grep -qE "^$key=" "$demo"; then
      printf '%s\n' "$line" >> "$demo"
    fi
  done
  sed -i -E '/^BINANCE_DEMO_(KEY|SECRET)=/d' "$shared"
  chown trader:trader "$shared" "$demo" 2>/dev/null || true
  chmod 600 "$shared" "$demo"
}
split_demo_keys
shopt -s nullglob
for f in "$CONF_DIR"/*-paper.env; do
  rename_file "$f" "${f%-paper.env}-demo.env"
done
for f in "$CONF_DIR"/*-prod.env; do
  rename_file "$f" "${f%-prod.env}-live.env"
done
for f in "$CONF_DIR"/*.env "$APP_DIR"/.env "$APP_DIR"/.env.demo "$APP_DIR"/.env.live; do
  [[ -f "$f" ]] || continue
  sed -i \
    -e 's|/var/lib/tradingbot/paper|/var/lib/tradingbot/demo|g' \
    -e 's|/var/lib/tradingbot/prod|/var/lib/tradingbot/live|g' \
    -e 's|^BOT_ENV=paper$|BOT_ENV=demo|' \
    -e 's|^BOT_ENV=prod$|BOT_ENV=live|' \
    -e 's|^MODE=paper$|MODE=demo|' \
    -e 's|^MODE=prod$|MODE=live|' \
    "$f"
done

for unit in "${LEGACY_UNITS[@]}"; do
  base=${unit#tradingbot@}
  base=${base%.service}
  case "$base" in
    *-paper) newbase=${base%-paper}-demo ;;
    *-prod)  newbase=${base%-prod}-live ;;
    *) continue ;;
  esac
  if [[ ${WAS_ACTIVE[$unit]:-} == active && -f "$CONF_DIR/$newbase.env" ]]; then
    log "unit $unit -> tradingbot@$newbase.service"
    systemctl enable --now "tradingbot@$newbase.service"
  fi
done

rm -f /etc/systemd/system/tradingbot-forward.service /etc/systemd/system/tradingbot-web-prod.service
systemctl daemon-reload

log 'done'
echo 'next: sudo bash /opt/trading-bot/deploy/update.sh'
echo 'then: sudo bash /opt/trading-bot/deploy/public_web.sh <base-hostname>'
