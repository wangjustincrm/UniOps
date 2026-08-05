# UniOps — Production Deployment Guide

> **This guide covers production deployment only.**
> For local development, see [README.md](README.md) and use `docker compose -f docker-compose.dev.yml up`.

---

## Server Layout (Phase 1 — Current)

| Server | Role | Services |
|--------|------|---------|
| **DB Server** | Database only | PostgreSQL 15 (:5432, internal network only), Redis 7 (:6379, internal) |
| **App Server 1** | Core backend | epms-api (:8000), approval-api (:8003), mdm-api (:8002), finance-api (:8004) |
| **File Server** | File storage | file-api (:8005) + `/var/uniops/file-storage/` disk mount |
| **Web Server** | Frontend + proxy | Nginx reverse proxy, EPMS frontend (:5173 → built static files) |

## Server Layout (Phase 2 — OA Module)

| Server | Role | Services |
|--------|------|---------|
| **DB Server** | *(same as Phase 1)* | PostgreSQL, Redis |
| **App Server 1** | *(same as Phase 1)* | epms-api, approval-api, mdm-api, finance-api |
| **App Server 2** | OA backend | expense-api (:8006) |
| **File Server** | *(same as Phase 1)* | file-api (:8005) |
| **Web Server** | Frontend + proxy | Nginx, EPMS (:5173), OA (:5175), Portal (:5174) |

---

## Per-Service Setup

Run these steps on the appropriate server for each service.

### Prerequisites

```bash
# Install Python 3.12
sudo apt install python3.12 python3.12-venv

# Install Node.js 20 (web server only, for frontend build)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt install nodejs
```

### Backend Service Setup (App Server)

```bash
# 1. Clone / pull the repo
git pull origin main

# 2. Create virtual environment
cd /opt/uniops/{service-name}
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
nano .env  # fill in production values (DB host, JWT secret, etc.)

# 5. Run migrations
alembic upgrade head
# ⚠️ Ordering: finance-api's alembic MUST run before deploying new versions of
# approval-api / expense-api — they INSERT into posting_events/posting_lines,
# which are created by finance-api migration 0002_posting_events.

# 6. Start service (use systemd in production — see below)
uvicorn app.main:app --host 0.0.0.0 --port {PORT}
```

### Frontend Build (Web Server)

```bash
cd /opt/uniops/epms
cp .env.example .env
# Set VITE_API_URL to the internal app server address
nano .env

npm install
npm run build
# Output: dist/ — serve via Nginx
```

---

## Docker Deployment — Application Server (recommended)

> Topology: **one application server** runs all frontends **and all backends**
> (`docker-compose.prod.yml`). PostgreSQL + Redis are on the **DB server**;
> `file-api` is on the **File server** — both reached over the internal network
> via `.env`. Frontends are multi-stage images (Vite build → `nginx:alpine`);
> backends use their existing Dockerfiles. Released via a registry — no Node or
> Python build toolchain is needed on the app server itself.

### Why build from the repo root

The frontends share the `@uniops/shell` workspace package (`packages/shell`).
Each frontend `Dockerfile` therefore builds with the **repo root** as context so
the package resolves cleanly — no per-server `npm install`, no `--install-links`
gymnastics. Build args inject the browser-facing `VITE_*` URLs (Vite inlines them
at build time).

### Topology: GHCR registry + subdomains behind a single Caddy 443 edge

- **Images:** GitHub Container Registry — `ghcr.io/wangjustincrm/uniops-*:<tag>`.
- **Single entry:** the **Caddy `edge`** service on the app server (`10.10.50.65`)
  listens on **443**, terminates TLS with the company wildcard cert
  (`./certs/{fullchain,privkey}.pem`, covers `*.canadaroyalmilk.com`), and routes
  subdomains to the containers (see `Caddyfile`):

  | Subdomain (`.canadaroyalmilk.com`) | → container        |
  |------------------------------------|--------------------|
  | `portal`/`epms`/`oa`/`vms`/`finance`/`mrp` | the web images |
  | `epms-api`                         | epms-api `8000`    |
  | `oa-api`                           | expense-api `8006` |
  | `vms-api`                          | vms-api `8008`     |
  | `finance-api`                      | finance-api `8004` |
  | `budget-api`                       | budget-api `8007`  |
  | `mdm-api`                          | mdm-api `8002`     |
  | `mrp-api`                          | mrp-api `8011`     |
  | `files`                            | File server `10.10.50.66:8005` |

  (`booking`/`booking-api` also route through the same edge — see "Booking
  Module Release Steps" below for their own dedicated DNS step; omitted from
  this base table for the same reason `mrp`/`mrp-api` were originally, before
  this update — each module's release-steps section is where its DNS gets
  called out at the time it's added, and this base table lags behind unless
  someone remembers to circle back and add it here too.)

  `approval-api`/`identity-api` are server-to-server only (no subdomain). The
  browser-facing `*_URL` are baked into the bundles at **build time** as
  `https://<sub>.canadaroyalmilk.com`, so both internal and external users use the
  same HTTPS domains.
- **Public exposure:** the firewall **Server Mapping** forwards **only**
  public `45.78.113.218:443` → `10.10.50.65:443` (the edge). No other port is
  exposed. The web/api services still publish their ports for on-box debugging but
  public traffic enters only through Caddy.

### Prerequisites

- A **GitHub Personal Access Token** with `write:packages` (build host) /
  `read:packages` (app server).
- **TLS cert** for `*.canadaroyalmilk.com` placed at `./certs/fullchain.pem` +
  `./certs/privkey.pem` on the app server (see `certs/README.md`).
- **Firewall Server Mapping:** public `45.78.113.218:443` → `10.10.50.65:443`
  (TCP). Enable NAT **hairpin/loopback** so internal users hitting the public IP
  reach the edge too (or use split-DNS — see DNS below).
- **DNS** A records (→ `45.78.113.218`): `portal`, `epms`, `oa`, `vms`, `finance`,
  `mrp`, `epms-api`, `oa-api`, `vms-api`, `finance-api`, `budget-api`, `mdm-api`,
  `mrp-api`, `files` — each `.canadaroyalmilk.com`. (Use specific records, **not**
  a wildcard on the company apex.) Internal: rely on firewall hairpin, or add the
  same names in the internal DNS pointing at `10.10.50.65` (split-DNS). (`booking`/
  `booking-api` need their own A records too — see "Booking Module Release
  Steps" below, which was written with its own explicit DNS step.)
- App server `10.10.50.65` can reach the **DB server** (`${DB_HOST}:5432` + Redis
  `:6379`) and **File server** (`10.10.50.66:8005`) — add `10.10.50.65` to
  **pg_hba.conf + ufw** on the DB server.

### 1. Build + push (build host — your dev machine or CI)

```bash
cp .env.prod.example .env        # fill REGISTRY=ghcr.io/wangjustincrm, DB_PASSWORD,
                                 # JWT_SECRET_KEY; *_URL already use the HTTPS subdomains
echo "<GHCR_PAT>" | docker login ghcr.io -u wangjustincrm --password-stdin
export TAG=$(git rev-parse --short HEAD)

docker compose -f docker-compose.prod.yml build      # builds all web + api images
docker compose -f docker-compose.prod.yml push       # pushes ghcr.io/wangjustincrm/uniops-*:${TAG}
```

> If parallel builds hit PyPI read-timeouts, build serially:
> `for s in $(docker compose -f docker-compose.prod.yml config --services); do docker compose -f docker-compose.prod.yml build "$s" || docker compose -f docker-compose.prod.yml build "$s"; done`
>
> First push: GHCR packages default to **private**. Keep them private (the app
> server logs in to pull) or mark each Public in GitHub → Packages.

### Two image sets (same code, different baked URLs)

| Set | Tag | Baked URLs | Deploy with |
|-----|-----|------------|-------------|
| **LAN** (current) | `…-lan` (e.g. `90303d8-lan`) | `http://10.10.50.65:<port>` | plain `up -d` (no edge) |
| **Domain** (deferred external) | `…` (e.g. `90303d8`) | `https://*.canadaroyalmilk.com` | `--profile edge up -d` + certs |

Backend images are identical across sets (no baked URLs; `ALLOWED_ORIGINS` is a
runtime env). Only the 5 web images differ. The Caddy `edge` is gated behind the
`edge` compose profile, so a plain `up -d` runs LAN mode without it.

### 2a. Deploy — LAN mode (internal IP, no domain/TLS) — CURRENT

```bash
git pull origin main
cp .env.lan.example .env         # TAG=90303d8-lan, DB_PASSWORD, JWT_SECRET_KEY
echo "<GHCR_PAT>" | docker login ghcr.io -u wangjustincrm --password-stdin

docker compose -f docker-compose.prod.yml pull
./migrate-prod.sh                # ordered alembic — finance-api FIRST
docker compose -f docker-compose.prod.yml up -d          # NO --profile edge

docker compose -f docker-compose.prod.yml ps             # all healthy?
```

Internal browser → `http://10.10.50.65:5174` (Portal) → log in → tiles jump to
the other modules at `http://10.10.50.65:<port>`.

### 2b. Deploy — Domain mode (external, when ready)

```bash
git pull origin main
cp .env.prod.example .env        # TAG=90303d8 (the https set), DB_PASSWORD, JWT_SECRET_KEY
mkdir -p certs                   # copy fullchain.pem + privkey.pem into ./certs
echo "<GHCR_PAT>" | docker login ghcr.io -u wangjustincrm --password-stdin

docker compose -f docker-compose.prod.yml pull
./migrate-prod.sh
docker compose -f docker-compose.prod.yml --profile edge up -d   # WITH Caddy

docker compose -f docker-compose.prod.yml logs --tail=30 edge    # cert + sites loaded?
```

Then add the firewall mapping + DNS (see Prerequisites) and open
`https://portal.canadaroyalmilk.com`.

**Rollback:** set `TAG` to a previous git short SHA in `.env`, then
`docker compose -f docker-compose.prod.yml pull && up -d` (re-run `migrate-prod.sh`
only if the new release added migrations — migrations are forward-only).

**Migrations** run via `migrate-prod.sh`: `alembic upgrade head` per service in a
safe order — **finance-api first** (it creates `posting_events`/`posting_lines`
that expense-api writes into). `approval-api` has no migrations. Containers run
uvicorn only (no auto-migrate), so this step is explicit and ordered.

> ⚠️ The `POSTGRES_PASSWORD` env is **required** on budget/finance/mdm/vms-api —
> they compute `DATABASE_URL` from `POSTGRES_*` and ignore the env `DATABASE_URL`.
> The shared `x-db-env` anchor sets it for every service.

### Local build sanity check (no registry needed)

```bash
docker build -f vms/Dockerfile -t uniops-vms-web:local .   # context = repo root
docker run --rm -p 8099:80 uniops-vms-web:local            # open http://localhost:8099
```

---

## Shared Requirements (All App Servers)

All backend services must share these values in their `.env`:

```bash
# Same secret across ALL services (shared JWT authentication)
JWT_SECRET_KEY=<same-strong-secret-on-all-servers>
JWT_ALGORITHM=HS256

# Internal network addresses (not public-facing)
DATABASE_URL=postgresql+asyncpg://epms:{password}@{db-server-internal-ip}:5432/epms
```

Additional per-service URLs (use internal network IPs/hostnames):

| Service | Needs access to |
|---------|----------------|
| `epms-api` | PostgreSQL, Redis, `approval-api`, `file-api` |
| `approval-api` | PostgreSQL |
| `expense-api` | PostgreSQL, `approval-api`, `file-api`, `epms-api` |
| `file-api` | PostgreSQL |
| `mdm-api` | PostgreSQL |
| `finance-api` | PostgreSQL, `epms-api` |

---

## Nginx Configuration (Web Server)

```nginx
# /etc/nginx/sites-available/uniops

server {
    listen 80;
    server_name uniops.canadaroyalmilk.com;

    # EPMS Frontend (built static files)
    location / {
        root /opt/uniops/epms/dist;
        try_files $uri $uri/ /index.html;
    }

    # EPMS API proxy
    location /api/ {
        proxy_pass http://{app-server-1-internal-ip}:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # File Server proxy
    location /files/ {
        proxy_pass http://{file-server-internal-ip}:8005;
    }
}
```

---

## Systemd Service (per backend service)

Create `/etc/systemd/system/uniops-epms-api.service`:

```ini
[Unit]
Description=UniOps EPMS API
After=network.target

[Service]
User=uniops
WorkingDirectory=/opt/uniops/epms-api
EnvironmentFile=/opt/uniops/epms-api/.env
ExecStart=/opt/uniops/epms-api/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable uniops-epms-api
sudo systemctl start uniops-epms-api
sudo systemctl status uniops-epms-api
```

Repeat for each backend service with the appropriate port and directory.

---

## Health Check (Remote)

```bash
# Check all services on production app server
EPMS_HOST={app-server-ip} ./check-health.sh
```

---

## Booking Module Release Steps

The `booking-api` and `booking-web` services follow the same build/push/deploy pattern as VMS.
Additional one-time steps are required before the first booking release:

1. **New services** — `booking-api` (`:8010`) and `booking-web` are added to `docker-compose.prod.yml`.
   They are built and pushed alongside all other services in the standard build step.

2. **Portal rebuild** — `portal-web` must be rebuilt with `VITE_BOOKING_URL=https://booking.canadaroyalmilk.com`
   injected as a build arg (already present in `docker-compose.prod.yml`). A fresh portal image is
   required so the Booking tile appears in the Portal home page.

3. **Alembic migration — btree_gist prerequisite** — `booking-api`'s first migration creates the
   `btree_gist` extension and the `no_double_booking` exclusion constraint.
   `CREATE EXTENSION` requires superuser privilege; if the app DB user (`epms`) is not a superuser,
   run the following as the `postgres` superuser on the DB server **before** running `migrate-prod.sh`:
   ```sql
   CREATE EXTENSION IF NOT EXISTS btree_gist;
   ```
   Confirm with: `\dx` in psql — `btree_gist` must be listed.  Only needed once per DB.
   After the extension exists, `migrate-prod.sh` (which runs `alembic upgrade head` inside the
   `booking-api` container) will complete without privilege errors.
   No ordering dependency with other services (booking-api has its own tables only).

4. **File server: add booking origin to file-api ALLOWED_ORIGINS** (required for room image upload/preview)
   On the File server (`10.10.50.66`), edit `file-api/.env.prod` and add
   `https://booking.canadaroyalmilk.com` to the `ALLOWED_ORIGINS` list, then restart file-api:
   ```bash
   # On 10.10.50.66
   nano /opt/uniops/file-api/.env.prod   # add https://booking.canadaroyalmilk.com to ALLOWED_ORIGINS
   systemctl restart uniops-file-api
   ```
   Without this step, browser upload and image preview requests from the booking frontend will be
   CORS-blocked by file-api.

5. **Outlook real-invite verification (iMIP over SMTP)** — booking-api sends calendar invites as
   iMIP emails over SMTP (`multipart/alternative` with a `text/calendar; method=REQUEST` part),
   not via Microsoft Graph / Azure.  There are no Azure credentials involved.

   **Before running the gate test, confirm SMTP is configured** — booking-api resolves SMTP settings
   in this order:
   1. `booking_config.smtp_settings` (set via Booking → Admin → Settings → Email in the UI, or
      directly in the `booking_config` table).
   2. Fallback: shared `company_config` SMTP columns (`smtp_host`, `smtp_port`, `smtp_user`,
      `smtp_password`, `smtp_use_tls`, `smtp_from`) — the same settings used by other modules.

   If neither is configured, booking-api logs the notification attempt and degrades gracefully
   (no email sent, `sync_status = "failed"`).  Configure at least the shared company SMTP before
   the gate test.

   **Organizer mode** — by default booking-api uses `system` mode: the `From:` address is the
   configured SMTP sender, and the ORGANIZER in the iCalendar object is the booking creator's
   display name only.  There is no "send on behalf of" Exchange delegation — this is correct for
   the iMIP-over-SMTP design.

   **Gate test — run before going live:**
   1. Create a booking with yourself as organizer and attendee.
   2. Verify you receive an email with a `text/calendar` attachment and that
      **Outlook Classic Desktop** renders it as a calendar event with Accept/Decline buttons.
      (Web Outlook and mobile may render differently — Classic Desktop is the acceptance criterion.)
   3. PATCH the booking (e.g. change the title).  Verify Outlook updates the event
      (the iMIP SEQUENCE number increments → Outlook replaces the existing event).
   4. Cancel the booking.  Verify Outlook receives a `METHOD:CANCEL` iMIP message and
      removes the event from the calendar.

6. **DNS + Caddy** — `booking.canadaroyalmilk.com` and `booking-api.canadaroyalmilk.com` are already
   present in the `Caddyfile`. Add both A records (→ `45.78.113.218`) in the external DNS and the
   corresponding internal split-DNS entries (→ `10.10.50.65`).

---

## MRP Module Release Steps

`mrp-api` (`:8011`) and `mdm-api`'s BOM sync (`nc_bom*` raw mirror + canonical
`boms`/`bom_lines`/`bom_substitutes`) follow the same build/push/deploy pattern
as the other services. Two one-time steps are required for the first MRP
release:

1. **mrp-api is in `migrate-prod.sh`** — it owns its own `alembic_version_mrp`
   table (independent from `alembic_version_mdm`/the shared `alembic_version`),
   and its first migration has `down_revision=None`, so its position in the
   ordered `SERVICES` list is unconstrained. It IS included in the list —
   **do not remove it**: a release that ships the `mrp-api` image without
   running its migration deploys a service whose `/health` reports healthy
   with no tables underneath (every `wms_inventory_lots` read then 500s).

2. **WMS (Flux) Oracle read-only connection** — `mrp-api`'s inventory-lot sync
   needs `WMS_HOST` / `WMS_PORT` / `WMS_SERVICE` / `WMS_USER` / `WMS_PASSWORD`
   in `.env` (see `.env.prod.example` / `.env.lan.example`). `docker-compose.prod.yml`
   reads these as `${WMS_HOST:-}` etc. — leaving them blank does not fail the
   deploy, it just leaves the sync endpoint reporting "not configured" (503)
   until real values are filled in.

3. **Seed the 8 MRP + 1 mdm permission keys** — the MRP phase-0 permission
   keys (`mrp.demand.write`, `mrp.run.execute`, `mrp.proposal.confirm`,
   `mrp.proposal.export`, `mrp.exception.handle`, `mrp.param.write`,
   `mrp.report.view`, `mdm.bom.write`) are defined in code
   (`identity-api/scripts/seed_authz.py`'s `MODULE_BY_KEY`) but only exist in
   the DB — and therefore only appear in the Portal Access Control matrix and
   only gate anything — after this script has run against production:

   ```bash
   # On the app server, inside the identity-api container:
   docker compose -f docker-compose.prod.yml exec identity-api python -m scripts.seed_authz
   ```

   This is idempotent for *new* keys/roles (`ON CONFLICT DO NOTHING` on
   `permission_defs`/`role_defs`) and safe to run on every MRP-touching
   release. **Caveat — do not run it speculatively on unrelated releases**:
   `seed_authz.py` also re-inserts each role's *default* grants
   (`role_permissions`) for every key in `MODULE_BY_KEY`, and `ON CONFLICT DO
   NOTHING` cannot tell "this grant was never inserted" apart from "an admin
   explicitly unchecked this grant in the Access Control UI after a previous
   seed". Re-running the script will silently **resurrect** any default grant
   an admin has since revoked (for any of the keys in `MODULE_BY_KEY`, not
   just the new MRP ones) — it does not touch grants that were never part of
   `DEFAULTS`/`LOCKED` at all. Only run it when a release adds new keys/roles
   that actually need seeding, and re-check the Access Control matrix
   afterward for any revoked grant that came back.

4. **DNS + Caddy** — `mrp.canadaroyalmilk.com` and `mrp-api.canadaroyalmilk.com`
   are already present in the `Caddyfile` (same pattern as Booking's step 6
   above). Add both A records (→ `45.78.113.218`) in the external DNS and the
   corresponding internal split-DNS entries (→ `10.10.50.65`) — see
   Prerequisites' DNS list above, which now includes them (M13, final-phase
   review: this step and the Prerequisites DNS list previously omitted
   `mrp`/`mrp-api` entirely).

5. **Migration `0015` is DDL-only** (M8, final-phase review) — it adds
   columns/constraints but does not backfill or re-derive any data. A
   dev/staging DB that was already sitting at 0011-0014 before this release
   will apply 0015 cleanly, but the `boms`/`bom_lines` rows it already had
   from an earlier `POST /mdm/v1/boms/sync` keep serving the **batch-scaled**
   quantities that sync run computed — 0015's new columns/behavior only take
   effect for rows written by a sync that runs *after* 0015 is applied.
   **Run `POST /mdm/v1/boms/sync` again after migrating** any environment
   that had BOM data before this release, or the BOM Explorer / explosion
   endpoints will silently keep serving pre-0015 quantities until the next
   sync happens to run for some other reason. **Production is unaffected**:
   `boms` is created empty by migration `0011` in this same release, so
   there is no pre-existing data to be stale in the first place — this only
   matters for a dev/staging DB that had already synced BOMs before 0015
   landed.

---

## Security Notes

- PostgreSQL and Redis must only be accessible on the **internal network** (no public port exposure)
- `JWT_SECRET_KEY` must be the same strong secret on all servers — generate with:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
- `DEBUG=false` in all production `.env` files (disables Swagger docs)
- File storage directory (`/var/uniops/file-storage`) should be on a separate disk with regular backups
