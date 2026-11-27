#!/usr/bin/env bash
# Nightly database dump, 14-day retention.
set -euo pipefail

DEST=/var/backups/tradingbot
KEEP_DAYS=14

install -d -m 0750 "$DEST"
runuser -u postgres -- pg_dump --clean --if-exists --no-owner trading_bot \
  | gzip > "$DEST/trading_bot-$(date +%F).sql.gz"
find "$DEST" -name 'trading_bot-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
