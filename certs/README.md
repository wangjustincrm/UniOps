# TLS certificates (app server only — do NOT commit the key)

Place the company wildcard cert covering `*.canadaroyalmilk.com` here, named:

- `fullchain.pem` — server certificate **+ intermediate chain** (leaf first)
- `privkey.pem`   — the private key (keep secret; gitignored)

The `edge` (Caddy) service mounts this dir read-only at `/etc/caddy/certs`.
If your cert/key are in another format:

- `.pfx`/`.p12` → split out:
  `openssl pkcs12 -in cert.pfx -clcerts -nokeys -out fullchain.pem`
  `openssl pkcs12 -in cert.pfx -nocerts -nodes -out privkey.pem`
- separate `cert.crt` + `chain.crt` → concatenate (leaf then chain) into fullchain.pem.
