#!/usr/bin/env bash
#
# Let's Encrypt wildcard cert renewal for *.canadaroyalmilk.com (manual DNS-01).
#
# Rebel (the DNS registrar) has no API, so renewal is semi-manual: this script
# does everything EXCEPT writing the _acme-challenge TXT record, which you paste
# into Rebel by hand. Two steps, a few minutes, every ~90 days.
#
#   sudo ./renew-cert.sh request   # prints the 2 TXT values to add at Rebel
#   # → add both as TXT on _acme-challenge.canadaroyalmilk.com, wait ~5 min, verify:
#   #     nslookup -type=TXT _acme-challenge.canadaroyalmilk.com 8.8.8.8
#   sudo ./renew-cert.sh finish    # completes issuance, installs cert, reloads Caddy
#
#   sudo ./renew-cert.sh status    # show current cert expiry
#
# State (LE account + order) persists in ./acme-state. The issued cert is written
# to ./certs/{fullchain,privkey}.pem — the same files the Caddy edge mounts.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
STATE="$DIR/acme-state"
CERTS="$DIR/certs"
EMAIL="wangjustin@canadaroyalmilk.com"
DOMAINS=(-d "*.canadaroyalmilk.com" -d "canadaroyalmilk.com")
MANUAL=--yes-I-know-dns-manual-mode-enough-go-ahead-please

acme() {
  docker run --rm -v "$STATE:/acme.sh" -v "$CERTS:/certs" neilpang/acme.sh "$@"
}

case "${1:-}" in
  request)
    mkdir -p "$STATE" "$CERTS"
    # Fresh order; manual mode prints the TXT records and stops.
    acme --issue --dns "${DOMAINS[@]}" "$MANUAL" --force \
         --server letsencrypt --accountemail "$EMAIL" || true
    cat <<'EOF'

──────────────────────────────────────────────────────────────────────────────
NEXT:
  1. At Rebel DNS, set TWO TXT records on host  _acme-challenge
     (i.e. _acme-challenge.canadaroyalmilk.com) to the TWO values printed above.
  2. Wait ~5 min, then verify both new values are live:
       nslookup -type=TXT _acme-challenge.canadaroyalmilk.com 8.8.8.8
  3. Run:  sudo ./renew-cert.sh finish
──────────────────────────────────────────────────────────────────────────────
EOF
    ;;
  finish)
    acme --renew --dns "${DOMAINS[@]}" "$MANUAL" --server letsencrypt
    acme --install-cert -d "*.canadaroyalmilk.com" \
         --fullchain-file /certs/fullchain.pem \
         --key-file /certs/privkey.pem
    chmod 600 "$CERTS/privkey.pem"
    docker compose -f "$DIR/docker-compose.prod.yml" restart edge
    echo ">>> Renewed, installed, and Caddy reloaded. New expiry:"
    openssl x509 -in "$CERTS/fullchain.pem" -noout -subject -enddate
    echo ">>> You can now delete the _acme-challenge TXT records at Rebel."
    ;;
  status)
    openssl x509 -in "$CERTS/fullchain.pem" -noout -subject -issuer -enddate 2>/dev/null \
      || echo "No cert at $CERTS/fullchain.pem"
    ;;
  *)
    echo "Usage: sudo $0 {request|finish|status}"
    exit 1
    ;;
esac
