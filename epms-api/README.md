# EPMS API

FastAPI + PostgreSQL + Redis backend for the EPMS procurement management system.

> **Deployment model:**
> - **Development:** Docker Compose (`docker compose up --build`) — all services on one machine
> - **Production:** Distributed servers — DB server, app server, file server deployed separately.
>   Docker Compose is **not used** in production. See [DEPLOYMENT.md](../DEPLOYMENT.md).

## Quick Start — Local Development (Docker)

```bash
docker compose up --build
```

- API: <http://localhost:8000>
- Swagger: <http://localhost:8000/docs> (requires `DEBUG=true` in `.env`)
- Health: <http://localhost:8000/health> · `/health/db` · `/health/redis`

## Quick API Test

```bash
# 1. Get a token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@epms.local","password":"Admin@epms2026"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Call an endpoint
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/pr
```

## Development Mode (hot-reload)

Use this when you want fast iteration with file watching:

### 1. Start infrastructure

```bash
docker compose up postgres redis -d
```

### 2. Create virtual environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements-dev.txt
```

### 4. Configure environment

```bash
cp .env.example .env
# Defaults work for local Docker. Set DEBUG=true to enable Swagger docs.
```

### 5. Run migrations

```bash
alembic upgrade head
```

### 6. Start the API

```bash
uvicorn app.main:app --reload
```

## Common commands

```bash
# Create a new migration
alembic revision --autogenerate -m "add users table"

# Apply migrations
alembic upgrade head

# Rollback one step (DEV ONLY — may cause data loss in production)
alembic downgrade -1

# Run tests
pytest

# Lint
ruff check .
```

## Project Structure

```
app/
├── api/v1/          # Route handlers
├── core/
│   ├── config.py    # pydantic-settings (reads .env)
│   ├── security.py  # JWT, bcrypt, TOTP
│   └── deps.py      # FastAPI dependencies & RBAC
├── db/
│   ├── base.py      # SQLAlchemy declarative base + mixins
│   ├── session.py   # Async engine & session factory
│   └── redis.py     # Redis client
├── models/          # SQLAlchemy ORM models
├── schemas/         # Pydantic request/response schemas
├── crud/            # Database access layer
├── services/        # Business logic layer
└── main.py          # App factory
```

## Environment Variables

See [.env.example](.env.example) for all available settings.
