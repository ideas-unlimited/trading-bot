#!/usr/bin/env bash
# Publish both dashboards behind HTTPS + basic auth.
#
#   sudo bash deploy/public_web.sh <base-hostname> [username]
#
# Example base hostname without a domain: 139-99-236-166.sslip.io.  Two
# site blocks are rendered from that same base:
#   demo.<base> -> 127.0.0.1:9000   (DEMO - simulated fills)
#   live.<base>  -> 127.0.0.1:9001   (LIVE - real money, venue not configured)
# and <base> itself redirects to demo.  sslip.io resolves those subdomains
# to the same IP, so Let's Encrypt issues real certificates for both.
set -euo pipefail
trap 'echo "public_web.sh failed at line $LINENO" >&2' ERR

BASE=${1:?usage: public_web.sh <hostname> [username]}
BASE=${BASE#http://}; BASE=${BASE#https://}; BASE=${BASE%/}
DEMO_DOMAIN="demo.$BASE"
LIVE_DOMAIN="live.$BASE"
LEGACY_DOMAIN="$BASE"

# Keep the login already published; only mint a password on first run or
# when WEB_PASSWORD says so.  Re-running must never lock the owner out.
EXIST_USER=$(sed -n '/basic_auth {/{n;s/^[[:space:]]*\([^[:space:]]*\).*/\1/p;q}' /etc/caddy/Caddyfile 2>/dev/null || true)
USER_NAME=${2:-${EXIST_USER:-trader}}

if [[ ${EUID:-1} -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi

if ! command -v caddy >/dev/null 2>&1; then
  echo "== installing caddy"
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gpg >/dev/null
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy >/dev/null
fi

EXIST_HASH=$(grep -oE '\$2[aby]\$[^[:space:]]+' /etc/caddy/Caddyfile 2>/dev/null | head -n1 || true)
SHOWN_PASS=""
if [[ -n "${WEB_PASSWORD:-}" ]]; then
  PASS=$WEB_PASSWORD
  HASH=$(caddy hash-password --plaintext "$PASS" | tr -d '\n')
  SHOWN_PASS=$PASS
elif [[ -n "$EXIST_HASH" ]]; then
  HASH=$EXIST_HASH
else
  PASS=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | cut -c1-20)
  HASH=$(caddy hash-password --plaintext "$PASS" | tr -d '\n')
  SHOWN_PASS=$PASS
fi

install -d -o caddy -g caddy /var/log/caddy
TEMPLATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/Caddyfile.template"
if [[ ! -f "$TEMPLATE" ]]; then
  echo "missing $TEMPLATE" >&2
  exit 1
fi
sed -e "s|{{DEMO_DOMAIN}}|$DEMO_DOMAIN|" \
    -e "s|{{LIVE_DOMAIN}}|$LIVE_DOMAIN|" \
    -e "s|{{LEGACY_DOMAIN}}|$LEGACY_DOMAIN|" \
    -e "s|{{USER}}|$USER_NAME|" \
    -e "s|{{HASH}}|$HASH|" \
  "$TEMPLATE" > /etc/caddy/Caddyfile

systemctl enable caddy >/dev/null 2>&1 || true
systemctl restart caddy
ufw allow 80/tcp >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true

echo "== waiting for certificates"
for domain in "$DEMO_DOMAIN" "$LIVE_DOMAIN"; do
  for _ in $(seq 1 20); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "https://$domain/" 2>/dev/null || echo 000)
    [[ "$code" == "200" || "$code" == "401" ]] && break
    sleep 3
  done
done

echo
echo "DEMO  : https://$DEMO_DOMAIN/"
echo "LIVE  : https://$LIVE_DOMAIN/"
echo "legacy: https://$LEGACY_DOMAIN/  -> redirects to demo"
echo "user  : $USER_NAME"
if [[ -n "$SHOWN_PASS" ]]; then
  echo "pass  : $SHOWN_PASS"
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -u "$USER_NAME:$SHOWN_PASS" "https://$DEMO_DOMAIN/" || echo 000)
  echo "check : demo HTTP $CODE"
else
  echo "pass  : unchanged"
fi
