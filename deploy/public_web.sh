#!/usr/bin/env bash
# Publish the dashboard behind HTTPS + basic auth.
#
#   sudo bash deploy/public_web.sh <hostname> [username]
#
# Example hostname without a domain: 139-99-236-166.sslip.io (sslip.io maps
# the dashes back to the IP, so Let's Encrypt can issue a real certificate).
# The app itself keeps listening on 127.0.0.1:9000; only Caddy is exposed.
set -euo pipefail
trap 'echo "public_web.sh failed at line $LINENO" >&2' ERR

DOMAIN=${1:?usage: public_web.sh <hostname> [username]}
USER_NAME=${2:-trader}

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

PASS=${WEB_PASSWORD:-$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | cut -c1-20)}
HASH=$(caddy hash-password --plaintext "$PASS" | tr -d '\n')

install -d -o caddy -g caddy /var/log/caddy
TEMPLATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/Caddyfile.template"
if [[ ! -f "$TEMPLATE" ]]; then
  echo "missing $TEMPLATE" >&2
  exit 1
fi
sed -e "s|{{DOMAIN}}|$DOMAIN|" -e "s|{{USER}}|$USER_NAME|" -e "s|{{HASH}}|$HASH|" \
  "$TEMPLATE" > /etc/caddy/Caddyfile

systemctl enable caddy >/dev/null 2>&1 || true
systemctl restart caddy
ufw allow 80/tcp >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true

echo "== waiting for the certificate"
for _ in $(seq 1 20); do
  if curl -fsS -o /dev/null --max-time 5 "https://$DOMAIN/" 2>/dev/null; then break; fi
  if [[ $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "https://$DOMAIN/" 2>/dev/null) == "401" ]]; then break; fi
  sleep 3
done

CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -u "$USER_NAME:$PASS" "https://$DOMAIN/" || echo 000)
echo
echo "URL  : https://$DOMAIN/"
echo "user : $USER_NAME"
echo "pass : $PASS"
echo "check: HTTP $CODE with credentials"
