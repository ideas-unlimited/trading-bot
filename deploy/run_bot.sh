#!/usr/bin/env bash
# systemd entry point for one bot: deploy/run_bot.sh <strategy>-<env>
# Reads /etc/tradingbot/<name>.env and execs the live loop in the foreground.
# Naming convention: <strategy>-<env>, e.g. capitulation_dip_uni-paper.
set -euo pipefail

name=${1:?usage: run_bot.sh <strategy>-<env>  (e.g. capitulation_dip_uni-paper)}
conf="/etc/tradingbot/${name}.env"
[[ -r "$conf" ]] || { echo "missing $conf" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
. "$conf"
set +a

# Durable log dir (see tradingbot@.service StandardOutput): one folder per
# world so paper and prod bot logs cannot mix.
env_name=${BOT_ENV:-paper}
LOG_DIR="/var/log/tradingbot/${env_name}"
mkdir -p "$LOG_DIR"
chown trader:trader "$LOG_DIR" 2>/dev/null || true
export LOG_DIR

cd /opt/trading-bot
# Risk knobs default to BotConfig's own defaults, so a bot that sets nothing
# behaves exactly as before.  A per-symbol sleeve sets them to the profile its
# backtest used (see deploy/README.md, "Add a bot").
exec /opt/trading-bot/.venv/bin/bot live \
  --symbol "${SYMBOL}" \
  --interval "${INTERVAL}" \
  --strategy "${STRATEGY}" \
  --capital "${CAPITAL}" \
  --state "${STATE}" \
  --poll "${POLL:-60}" \
  --heartbeat-url "${HEARTBEAT_URL:-}" \
  --risk-per-trade "${RISK_PER_TRADE:-0.01}" \
  --max-position-pct "${MAX_POSITION_PCT:-0.95}" \
  --max-drawdown-stop "${MAX_DRAWDOWN_STOP:-0.10}"
