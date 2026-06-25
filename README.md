# UniOps — Enterprise Management Platform

Internal platform for Canada Royal Milk. Replaces legacy NC65 ERP modules incrementally.

> **Deployment model:**
> - **Development:** Docker Compose — all services on one machine (see below)
> - **Production:** Distributed servers — DB server, app server, file server, web server
> - Docker Compose is **not used** in production. See [DEPLOYMENT.md](DEPLOYMENT.md).

---

## Quick Start — Local Development

**Prerequisites:** Docker Desktop

```bash
# 1. Start all services
docker compose -f docker-compose.dev.yml up

# 2. Verify everything is healthy
./check-health.sh

# 3. Reset + seed database (first time or after a clean slate)
./reset-db.sh --seed

# 4. Run tests
./run_tests.sh
```

---

## Services

| Service | Port | Directory | Description |
|---------|------|-----------|-------------|
| EPMS API | :8000 | `epms-api/` | Procurement backend (PR/PO/GR/PA/Invoice) |
| Approval Engine | :8003 | `approval-api/` | Workflow execution engine |
| MDM Stub | :8002 | `mdm-api/` | Master data (vendors, departments, parts) |
| Finance Core | :8004 | `finance-api/` | Budget & AP payables |
| File Server | :8005 | `file-api/` | Attachment storage |
| EPMS Frontend | :5173 | `epms/` | Procurement UI |

**Phase 2 (in development):**

| Service | Port | Directory | Description |
|---------|------|-----------|-------------|
| Expense API | :8006 | `expense-api/` | Expense & payment management |
| OA Frontend | :5175 | `oa/` | Expense & payment UI |
| Portal | :5174 | `portal/` | Unified module launcher |

---

## Development Commands

```bash
# Start all services (dev mode with hot-reload)
docker compose -f docker-compose.dev.yml up

# Start only infrastructure (DB + Redis), run services locally
docker compose -f docker-compose.dev.yml up postgres redis

# Check all services are healthy
./check-health.sh

# Reset database (DEV ONLY — destroys all data)
./reset-db.sh           # drop + recreate + migrate
./reset-db.sh --seed    # also load seed data

# Run tests
./run_tests.sh

# View logs for a specific service
docker compose -f docker-compose.dev.yml logs -f epms-api
```

---

## Service READMEs

- [EPMS API](epms-api/README.md) — backend setup, migrations, Swagger docs
- [EPMS Frontend](epms/README.md) — Vite dev server, env vars
- [Adding a new expense form type](expense-api/README-DEV.md) *(Phase 2)*

---

## Architecture

```
Frontend (Browser)
  └── EPMS UI :5173 ──────────────► EPMS API :8000
  └── Portal  :5174                     │
  └── OA UI   :5175 ──────────────► Expense API :8006
                                        │
                    ┌───────────────────┤
                    ▼                   ▼
             Approval Engine :8003   MDM :8002
             Finance Core :8004      File Server :8005
                    │
                    ▼
              PostgreSQL :5432
              Redis :6379
```

**Accounting spine (Phase 0):** every money-moving action (PA processed, expense paid)
writes a row to `posting_events` / `posting_lines` (owned by finance-api, migration
`0002_posting_events`). Idempotent on `(source_doc_type, source_doc_id, event_type)`.
Query: `GET :8004/finance/v1/posting/events`. Future AP/AR/FA/GL modules replay these events.

**Unified payment executor (Phase 0-B1.5):** `POST :8004/finance/v1/payments/execute`
is THE single payment implementation (can_pay incl. role_management assignments,
status flip, `payment_records`, posting event, PA-PO invoice marking, task closing).
The three UI-facing entries forward to it: EPMS PA `action=process` (epms-api),
OA `POST /pa/{id}/pay` and `POST /expenses/{id}/pay` (expense-api). The approval
engine no longer handles payments.

**Identity (Phase 0-B4):** authentication lives in identity-api (:8009 —
login / email-OTP MFA / refresh blacklist / me / change-password). epms-api's
`/api/v1/auth/*` is a thin proxy kept for frontend compatibility. identity-api
also owns `sod_rules` (segregation-of-duties config; `self_payment` is enforced
by the payment executor) and the append-only `audit_log`.

**Production:** Each service runs on a dedicated server. See [DEPLOYMENT.md](DEPLOYMENT.md).
