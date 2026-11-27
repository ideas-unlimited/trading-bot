#!/usr/bin/env bash
# Read-only readiness checks for a fresh VPS.  Run this *before* install.sh:
#
#   bash deploy/preflight.sh
#
# Nothing is installed or changed.  Exits non-zero if any check fails.
set -uo pipefail

fails=0
pass() { printf '  \033[32mok\033[0m    %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fails=$((fails + 1)); }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }

printf '\n== machine\n'
printf '  %s %s, %s\n' "$(uname -s)" "$(uname -r)" "$(uname -m)"
[ -r /etc/os-release ] && . /etc/os-release
case "${ID:-}" in
  debian|ubuntu) pass "distribution ${ID} ${VERSION_ID:-}" ;;
  *) fail "installer supports Debian/Ubuntu, found '${ID:-unknown}'" ;;
esac

printf '\n== resources\n'
mem=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
[ "${mem:-0}" -ge 1800 ] && pass "RAM ${mem} MB" || fail "RAM ${mem:-0} MB (< 2 GB)"
disk=$(df -Pk / | awk 'NR==2{print int($4/1048576)}')
[ "${disk:-0}" -ge 5 ] && pass "free disk ${disk} GB" || fail "free disk ${disk:-0} GB (< 5 GB)"

printf '\n== python\n'
if command -v python3 >/dev/null 2>&1; then
  pyver=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
  pyok=$(python3 -c 'import sys;print(int(sys.version_info>=(3,11)))')
  [ "$pyok" = "1" ] && pass "python $pyver (needs >= 3.11)" || fail "python $pyver is older than 3.11"
else
  fail "python3 not installed"
fi
command -v git >/dev/null 2>&1 && pass "git present" || warn "git missing (installer adds it)"

printf '\n== network\n'
code() { curl -sS -o /dev/null -m 15 -w '%{http_code}' "$1" 2>/dev/null || echo 000; }
b=$(code https://api.binance.com/api/v3/ping)
case "$b" in
  200) pass "binance api reachable" ;;
  451) fail "binance answers 451: restricted location — this box cannot trade" ;;
  *)   fail "binance ping returned HTTP $b" ;;
esac
a=$(code "https://data.alpaca.markets/v2/stocks/bars?symbols=SPY&limit=1")
case "$a" in
  200|401|403) pass "alpaca market data reachable (HTTP $a)" ;;
  *)           fail "alpaca data returned HTTP $a" ;;
esac
g=$(code https://github.com)
[ "$g" != "000" ] && pass "github reachable (HTTP $g)" || fail "github unreachable"

printf '\n== services\n'
[ -d /run/systemd/system ] && pass "systemd running" || fail "systemd not running (needed for the units)"
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ':9000 '; then
  fail "port 9000 already in use"
else
  pass "port 9000 free"
fi
[ -d /opt/trading-bot ] && warn "/opt/trading-bot exists — install.sh will update in place"
[ "${EUID:-1}" -eq 0 ] && pass "running as root" || warn "not root: fine for checks, install.sh needs sudo"

printf '\n'
if [ "$fails" -eq 0 ]; then
  printf '\033[32mready\033[0m — run: sudo bash deploy/install.sh\n\n'
  exit 0
fi
printf '\033[31m%d check(s) failed\033[0m — fix these before install.sh\n\n' "$fails"
exit 1
