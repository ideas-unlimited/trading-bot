#!/usr/bin/env bash
# Nightly dump of the three databases, 14-day retention.
set -euo pipefail

DEST=/var/backups/tradingbot
KEEP_DAYS=14
DATABASES=${DATABASES:-"trading_common trading_paper trading_prod"}
STAMP=$(date +%F)

install -d -m 0750 "$DEST"
for db in $DATABASES; do
  tmp="$DEST/$db-$STAMP.sql.gz.tmp"
  runuser -u postgres -- pg_dump --clean --if-exists --no-owner "$db" | gzip > "$tmp"
  mv "$tmp" "$DEST/$db-$STAMP.sql.gz"
done
find "$DEST" -name 'trading_*-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
