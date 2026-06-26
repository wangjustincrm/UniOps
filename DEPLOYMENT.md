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

### Topology: GHCR registry + subdomain-per-module behind Caddy

- **Images:** GitHub Container Registry — `ghcr.io/wangjustincrm/uniops-*:<tag>`.
- **Domains:** one subdomain per module behind a **Caddy** edge proxy (automatic
  HTTPS). Frontends: `portal./epms./oa./vms./finance.<DOMAIN>`. Browser-facing
  APIs: `epms-api./oa-api./vms-api./finance-api./budget-api./mdm-api.<DOMAIN>`
  (`approval-api`/`identity-api` are server-to-server only — no subdomain).
- The `*_URL` values are baked into the static bundles at **build time**, so they
  must be the final public URLs before you build.

### Prerequisites

- A **GitHub Personal Access Token** with `write:packages` (build host) /
  `read:packages` (app server).
- **DNS A records** for each subdomain above → the app server's public IP, and
  ports **80 + 443** reachable (Caddy needs them for Let's Encrypt).
  *Internal domain (no public DNS):* in `Caddyfile`, give each site `tls internal`
  (Caddy local CA) or your own certs instead of automatic HTTPS.
- App server can reach the **DB server** (`${DB_HOST}:5432` + Redis `:6379`) and
  **File server** (`${FILE_SERVER_INTERNAL_URL}`) over the internal network
  (firewall: add the app server IP to pg_hba + ufw on the DB server).

### 1. Build + push (build host — your dev machine or CI)

```bash
cp .env.prod.example .env        # fill DOMAIN, REGISTRY=ghcr.io/wangjustincrm,
                                 # DB_PASSWORD, JWT_SECRET_KEY, and every *_URL
echo "<GHCR_PAT>" | docker login ghcr.io -u wangjustincrm --password-stdin
export TAG=$(git rev-parse --short HEAD)

docker compose -f docker-compose.prod.yml build      # builds all web + api images
docker compose -f docker-compose.prod.yml push       # pushes ghcr.io/wangjustincrm/uniops-*:${TAG}
```

> First push: GHCR packages default to **private**. Either keep them private (the
> app server logs in to pull) or mark each package Public in GitHub → Packages.

### 2. Deploy (application server)

```bash
git pull origin main             # gets docker-compose.prod.yml, Caddyfile, migrate-prod.sh
cp .env.prod.example .env        # same values as the build host (same TAG!)
echo "<GHCR_PAT>" | docker login ghcr.io -u wangjustincrm --password-stdin

docker compose -f docker-compose.prod.yml pull
./migrate-prod.sh                # ordered alembic — finance-api FIRST
docker compose -f docker-compose.prod.yml up -d

docker compose -f docker-compose.prod.yml ps         # all healthy?
docker compose -f docker-compose.prod.yml logs -f edge   # watch Caddy obtain certs
```

Open `https://portal.<DOMAIN>` → log in → tiles deep-link to the other modules.

**Rollback:** set `TAG` to a previous git short SHA in `.env`, then
`docker compose -f docker-compose.prod.yml pull && up -d` (re-run `migrate-prod.sh`
only if the new release added migrations — migrations are forward-only).

**Migrations** run via `migrate-prod.sh`: `alembic upgrade head` per service in a
safe order — **finance-api first** (it creates `posting_events`/`posting_lines`
that expense-api writes into). `approval-api` has no migrations. Containers run
uvicorn only (no auto-migrate), so this step is explicit and ordered.

The web/api services keep host-published ports (`5173-5177`, `8000-8009`) for
direct `IP:port` access during bring-up; once Caddy + DNS work, you can delete
those `ports:` so traffic only enters through Caddy (80/443).

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

## Security Notes

- PostgreSQL and Redis must only be accessible on the **internal network** (no public port exposure)
- `JWT_SECRET_KEY` must be the same strong secret on all servers — generate with:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
- `DEBUG=false` in all production `.env` files (disables Swagger docs)
- File storage directory (`/var/uniops/file-storage`) should be on a separate disk with regular backups
