#!/usr/bin/env bash
# systemd entry point for one bot: deploy/run_bot.sh <strategy>-<env>
# Reads /etc/tradingbot/<name>.env and execs the deploy loop in the foreground.
# Naming convention: <strategy>-<env>, e.g. capitulation_dip_uni-demo.
set -euo pipefail

name=${1:?usage: run_bot.sh <strategy>-<env>  (e.g. capitulation_dip_uni-demo)}
conf="/etc/tradingbot/${name}.env"
[[ -r "$conf" ]] || { echo "missing $conf" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
. "$conf"
set +a

# Make sure the durable log folder exists (tradingbot@.service appends
# /var/log/tradingbot/<bot>.log; logrotate handles rotation).
mkdir -p /var/log/tradingbot
chown trader:trader /var/log/tradingbot 2>/dev/null || true

cd /opt/trading-bot
# Fresh-state bootstrap: replay the fetched closed bars to learn whether the
# strategy is currently long, then open that exposure at today's price.  This
# is a new entry, not a copied historical fill.  A bot can opt out with
# STARTUP_REPLAY=0 in its env file; a manual `bot deploy` defaults to off.
export STARTUP_REPLAY="${STARTUP_REPLAY:-1}"
# Risk knobs default to BotConfig's own defaults, so a bot that sets nothing
# behaves exactly as before.  A per-symbol sleeve sets them to the profile its
# backtest used (see deploy/README.md, "Add a bot").
exec /opt/trading-bot/.venv/bin/bot deploy \
  --symbol "${SYMBOL}" \
  --interval "${INTERVAL}" \
  --strategy "${STRATEGY}" \
  --capital "${CAPITAL}" \
  --state "${STATE}" \
  --poll "${POLL:-60}" \
  --heartbeat-url "${HEARTBEAT_URL:-}" \
  --risk-per-trade "${RISK_PER_TRADE:-0.03}" \
  --max-position-pct "${MAX_POSITION_PCT:-0.95}" \
  --max-drawdown-stop "${MAX_DRAWDOWN_STOP:-0.99}"
