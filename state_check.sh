#!/usr/bin/env bash
echo 'placeholders in .env:'; sudo grep -c replace-me /opt/trading-bot/.env
echo 'units:'; systemctl is-enabled tradingbot@btc tradingbot@eth tradingbot@sol 2>&1
systemctl is-active tradingbot@btc tradingbot@eth tradingbot@sol 2>&1
echo 'cli:'; sudo /opt/trading-bot/.venv/bin/bot live --help 2>&1 | head -6
