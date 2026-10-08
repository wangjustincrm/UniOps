# TLS certificates — no longer used in normal operation

The `edge` (Caddy) service now obtains and renews a Let's Encrypt cert per
hostname **automatically** (see the header of `/Caddyfile`). Nothing needs to be
placed here.

This dir is still mounted read-only at `/etc/caddy/certs` purely as an
**emergency fallback**: if automatic issuance ever breaks, put a wildcard
`fullchain.pem` + `privkey.pem` here (e.g. via `renew-cert.sh`, manual DNS-01)
and point the `site_tls` snippet in the Caddyfile back at them:

    tls /etc/caddy/certs/fullchain.pem /etc/caddy/certs/privkey.pem

`privkey.pem` is gitignored — keep it `chmod 600`.
