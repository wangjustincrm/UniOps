# VMS — Sprint Plan

**Version:** 1.1
**Date:** 2026-05-30 (W8 ship)
**Scope:** Build VMS (Visitor Management System) as a native UniOps module — Sprints S1–S3, total 14 weeks.
**Reference Docs:** [VMS_PRD_Visitor_Management_System.md](VMS_PRD_Visitor_Management_System.md) V2.3 · [SPRINT.md](SPRINT.md) · PRD-PORTAL · approval-api engine doc
**Codebase root:** `c:/Project/uniops/`

---

## Progress Overview

```
S1 — MVP (Self-service closed loop)             ████████████ 100%  ✅ shipped 2026-05-30
S2 — Compliance (Approval + Health + Reports)   ░░░░░░░░░░░░   0%  📋 Planned
S3 — Polish (Alerts + Batch + Export)           ░░░░░░░░░░░░   0%  📋 Planned
```

| Sprint | Weeks | Goal | Status |
|--------|:-----:|------|:------:|
| **S1** | W1–W8 | MVP: visitor pre-registration, badge print (= check-in), QR check-out, basic audit log, integrated under Portal | ✅ |
| **S2** | W9–W12 | Compliance: health declaration + e-signature, GMP-zone approval via approval-api `vms_visit` doc_type, Portal Task Inbox integration, CFIA / GMP reports | 📋 |
| **S3** | W13–W14 | Polish: overtime alerts (email), batch check-out, data export (Excel / PDF), pilot rollout | 📋 |

---

## Pre-Sprint Architecture Decisions (locked by PRD V2.3)

| Decision | Value | Source |
|----------|-------|--------|
| vms-api port | **8008** (8007 is taken by budget-api) | PRD §6.2 |
| vms-frontend port | **5176** | PRD §6.8 |
| Database schema | `public` with `vms_*` table prefix (no separate `vms` schema) | PRD §5.3 |
| User master | Shared `public.users`; any authenticated UniOps user can act as Host | PRD §3 |
| Quality Manager | VMS-local role; roster in `vms_config.quality_manager_user_ids`; resolved per-visit via `vms_visits.quality_approver_id` | PRD §3, §6.2.1 |
| approval-api integration | cfm-style: add `vms_visit` doc_type to `_DOC_META` / `_WORKFLOW_DEFAULTS` / `_DOC_TYPES`; thin `Visit` mirror model in approval-api | PRD §6.2.1 |
| Approval endpoint | `POST /approval/v1/approvals/vms_visit/{visit_id}/action` (no `/submit`) | PRD §6.5.6 |
| Portal integration | VMS in MODULES sidebar + Task Inbox aggregates `doc_type="vms_visit"` + Portal Admin workflow tab | PRD §6.9 |

---

## S1 — MVP (Week 1–8, Self-Service Closed Loop)

> **Goal:** End of W8 — a Host can pre-register a visitor, print a badge in the browser (auto check-in), and scan a QR code to check out, all from VMS frontend under Portal SSO. No approval / no health declaration / no Teams notifications yet.
>
> **Out of scope for S1:** approval-api integration, health declarations, GMP-zone gates, audit dashboard drill-down, batch operations, CFIA reports.

### S1-A — vms-api Scaffold + DB Migration (Week 1)

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| Backend | Create `vms-api/` directory structure mirroring `expense-api/` (`app/{api,core,crud,db,models,schemas}`, `alembic/`, `Dockerfile`, `pyproject.toml`, `requirements.txt`) | `vms-api/**` | `vms-api/app/main.py` boots FastAPI on 8008 |
| Backend | Copy `app/db/base.py`, `app/db/session.py`, `app/core/{config,deps,security}.py` from `expense-api`; adjust `SERVICE_NAME=vms-api`, `PORT=8008` | `vms-api/app/db/`, `vms-api/app/core/` | `JWT_SECRET_KEY` shared with epms-api validates same tokens locally |
| Backend | `/health` and `/health/db` endpoints | `vms-api/app/api/v1/health.py` | `curl http://localhost:8008/health` → 200 |
| DB | Alembic init: `alembic/env.py` reads `DATABASE_URL`, points at shared `epms` DB | `vms-api/alembic/env.py` | `alembic upgrade head` runs against shared PG |
| DB | Migration 0001: create `vms_visitors`, `vms_visits`, `vms_badge_prints`, `vms_health_declarations`, `vms_audit_logs`, `vms_config` (all in `public` schema, `vms_` prefix); seed singleton `vms_config` row | `vms-api/alembic/versions/0001_initial.py` | All tables exist with real FKs to `public.users` (visit.host_id / created_by / quality_approver_id) |
| DB | Migration 0002: `REVOKE UPDATE, DELETE ON vms_audit_logs FROM epms;` (DB-level immutability per VMS-AU-003) | `vms-api/alembic/versions/0002_audit_immutable.py` | Direct `UPDATE vms_audit_logs ...` returns `permission denied` |
| Infra | `docker-compose.dev.yml`: append `vms-api` service block (port 8008, same env shape as budget-api) | `docker-compose.dev.yml` | `docker compose up vms-api` healthy |

**Acceptance:** `docker compose -f docker-compose.dev.yml up postgres vms-api` boots; `./check-health.sh` adds vms-api ✅; `psql` shows 6 `vms_*` tables.

---

### S1-B — Cross-Service Prerequisites (Week 2)

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| Backend | `epms-api`: add public `GET /api/v1/users/directory` returning `[{id, full_name, email, department_id}]` (paginated, `?search=`); auth = any authenticated user, NOT `AdminDep` | `epms-api/app/api/v1/users.py`, `epms-api/app/schemas/user.py` (new `UserBriefResponse`) | Requester role calling `/users/directory?search=jane` returns matches without 403 |
| Backend | `epms-api`: add `GET /api/v1/users/directory/{user_id}` for single brief lookup | `epms-api/app/api/v1/users.py` | Returns `UserBriefResponse` or 404; works for any authenticated role |
| Backend | CORS: append `http://localhost:5176` to default `ALLOWED_ORIGINS` in `epms-api/app/core/config.py` and to docker-compose env var across `epms-api`, `approval-api`, `file-api`, `expense-api`, `budget-api`, `mdm-api` | `epms-api/app/core/config.py`, `docker-compose.dev.yml` | Browser at 5176 calls 8000 / 8003 / 8005 / 8006 / 8007 without CORS error |
| Backend | `vms-api`: Pydantic schemas for `Visitor`, `Visit`, `BadgePrint`, `HealthDeclaration`, `AuditLog`, `VmsConfig` (Create / Update / Response variants) | `vms-api/app/schemas/*.py` | mypy passes; schemas mirror PRD §5.2 |
| Backend | `vms-api`: ORM models defined as in PRD §5.2 (Visitor, Visit, BadgePrint, HealthDeclaration, AuditLog, VmsConfig); enums = `VisitorType`, `VisitStatus`, `AccessArea`, `VisitPurpose`, `HealthDeclStatus` | `vms-api/app/models/*.py` | `alembic check` reports no drift |
| Backend | epms-api regression test: `pytest tests/test_users.py::test_directory_endpoint_open_to_all_roles` | `epms-api/tests/test_users.py` | Test passes for `requester`, `dept_manager`, `auditor`, `system_admin` |

**Acceptance:** Any UniOps role can curl `epms-api:8000/api/v1/users/directory?search=...` with their token and get results. VMS frontend (when scaffolded in S1-D) can reach all backend services without CORS issues.

---

### S1-C — Visitors + Visits CRUD (Week 3)

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| Backend | `vms-api`: CRUD endpoints `/api/v1/visitors` (list, get, create, patch) — any authenticated user can search; create is open to any UniOps role | `vms-api/app/api/v1/visitors.py`, `vms-api/app/crud/visitor.py` | PRD §6.5.1 endpoints all return 2xx for `requester` role |
| Backend | `vms-api`: CRUD endpoints `/api/v1/visits` (list, get, create appointment, patch); status starts as `confirmed` (no approval in S1); created_by + host_id populated from JWT / payload | `vms-api/app/api/v1/visits.py`, `vms-api/app/crud/visit.py` | PRD §6.5.2 endpoints all return 2xx |
| Backend | `vms-api`: `GET /api/v1/visits/active` — currently checked-in visitors | `vms-api/app/api/v1/visits.py` | Returns rows with `status=checked_in` |
| Backend | `vms-api`: `POST /api/v1/visits/{id}/cancel` — Host cancels own appointment before check-in | `vms-api/app/api/v1/visits.py` | Status flips to `cancelled` only if `status in (confirmed, pending_approval)` and `created_by == current_user` |
| Backend | `vms-api`: department-scoped visibility helper — `dept_manager` sees all visits in their `department_id`; others see only `created_by == self` OR `host_id == self`; `auditor` sees all | `vms-api/app/crud/visit.py` (`apply_visibility_scope`) | Manager A's `GET /visits` returns dept A visits, not dept B |
| Backend | `vms-api`: audit logging helper — every mutation writes to `vms_audit_logs` with old/new JSON, IP, user agent | `vms-api/app/crud/audit.py` | INSERT into `vms_audit_logs` after each PATCH; row count matches mutation count |
| Backend | Pytest: visitors + visits CRUD happy path + visibility scope | `vms-api/tests/test_visits.py`, `test_visitors.py` | `pytest vms-api` green |

**Acceptance:** Using only curl + Portal JWT, an end-to-end run can: create a visitor → create a visit appointment → list it → patch it → cancel it. Audit log shows 5 rows.

---

### S1-D — VMS Frontend Scaffold + Portal Integration (Week 4)

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| Frontend | Create `vms/` (Vite + React 19.2 + TS 6 + Tailwind 4.2 + Zustand 5 + React Query 5.100 + React Router 7.14 + Lucide 1.14) — clone `oa/`'s package.json/vite.config/tsconfig as template | `vms/package.json`, `vms/vite.config.ts`, `vms/tsconfig.json`, `vms/index.html` | `npm run dev -- --port 5176` boots without errors |
| Frontend | Auth scaffolding: localStorage key `vms-auth` (fallback `portal-auth`); `#__session=` hash handoff in `main.tsx`; redirect to Portal if no token; matches `oa/src/main.tsx` pattern | `vms/src/main.tsx`, `vms/src/lib/api.ts`, `vms/src/store/auth.ts` | Direct visit to `http://localhost:5176/` redirects to Portal; after Portal login lands back logged in |
| Frontend | `AppLayout.tsx` — sidebar nav (Today's Visits / All Visits / On-Site / New Visit / Dashboard / Admin); mobile collapsible (mirror `oa/src/components/layout/AppLayout.tsx`) | `vms/src/components/layout/AppLayout.tsx` | Renders on desktop + 375px mobile |
| Frontend | `api.ts` — typed React Query hooks: `useVisits`, `useVisit`, `useCreateVisit`, `useVisitors`, `useUserDirectory` (calls epms-api `/users/directory`) | `vms/src/services/api.ts` | All hooks have TS types matching backend schemas |
| Frontend | Pages: `VisitListPage`, `VisitCreatePage`, `VisitDetailPage` — minimum functionality (list, create with VisitorSearch + HostSearch components, view) | `vms/src/pages/{VisitListPage,VisitCreatePage,VisitDetailPage}.tsx`, `vms/src/components/{VisitorSearch,HostSearch}.tsx` | Host can create a visit through the UI; appears in list |
| Frontend | App.tsx routes per PRD §6.8 | `vms/src/App.tsx` | All routes navigable |
| Portal | `portal/src/pages/PortalHome.tsx`: extend `NAV_SECTIONS` MODULES with `{ label: 'VMS', icon: UserCheck, href: 'vms' }`; extend type comment to include `'vms' → top-level VMS landing`; add `vmsHref` computation in component (mirror epmsHref / oaHref); extend `resolveHref` with `'vms'` case | `portal/src/pages/PortalHome.tsx` | VMS card shows in Portal sidebar; clicking it lands on `http://localhost:5176/#__session=<jwt>` |
| Portal | docker-compose env: `portal-frontend` gets `VITE_VMS_URL=http://localhost:5176` and `VITE_VMS_API_URL=http://localhost:8008` | `docker-compose.dev.yml` | `process.env.VITE_VMS_URL` visible to PortalHome |
| Infra | docker-compose: add `vms-frontend` service block (port 5176, env `VITE_API_URL=http://localhost:8008`, `VITE_PORTAL_URL=http://localhost:5174`, `VITE_EPMS_API_URL=http://localhost:8000`); volume `vms_node_modules` | `docker-compose.dev.yml` | `docker compose up vms-frontend` healthy |

**Acceptance:** Login at Portal → click VMS card → land in VMS frontend already authenticated → create a visit appointment → see it in the list. No 401, no CORS errors.

---

### S1-E — Badge Printing (= Check-In) (Week 5)

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| Backend | `POST /api/v1/visits/{id}/print-badge` — atomic: insert `vms_badge_prints` row + update `vms_visits.status='checked_in'` + set `actual_arrival=now()`; returns the visit + badge data | `vms-api/app/api/v1/badge.py`, `vms-api/app/crud/badge.py` | First call sets check-in; subsequent calls insert reprint row without re-checking-in (`reprint_reason` required) |
| Backend | `GET /api/v1/visits/{id}/badge-history` — list all badge prints for a visit | `vms-api/app/api/v1/badge.py` | Returns chronological list |
| Backend | Badge templates stored in `vms_config.badge_templates: JSONB` (HTML + CSS strings, keyed by template name); `GET /api/v1/badge/templates`, `PUT /api/v1/badge/templates/{name}` (admin) | `vms-api/app/api/v1/badge.py` (templates router), `vms-api/app/crud/config.py` | Admin can edit template; non-admin gets 403 |
| Frontend | `BadgePrintPage.tsx`: rendered template with visitor name, company, date, area color band (green/yellow/orange/red per PRD §2.3 VMS-LB-002), QR code (visit UUID encoded via `qrcode.react`); print-only CSS via `@media print`; calls `window.print()` after mounting | `vms/src/pages/BadgePrintPage.tsx`, `vms/src/components/BadgePreview.tsx` | One A4 page renders 1–2 badge cards; print preview shows correct layout |
| Frontend | `VisitDetailPage`: "Print Badge" button → opens `BadgePrintPage` in new tab → confirms print → calls `print-badge` API → updates parent state | `vms/src/pages/VisitDetailPage.tsx`, `vms/src/pages/BadgePrintPage.tsx` | Visit status flips to `checked_in` after print confirmation |
| Frontend | "Instant Registration" path: `VisitCreatePage` has shortcut "Print Now" → creates visit + immediately calls print-badge | `vms/src/pages/VisitCreatePage.tsx` | Walk-in flow takes ≤ 3 clicks |
| Frontend | ID Verification checkbox (VMS-CI-005) — Host must check "ID Verified" before Print button enables | `vms/src/pages/VisitDetailPage.tsx` | Print button disabled until checkbox ticked |

**Acceptance:** Print → visit becomes `checked_in` → reprint button works without re-checking-in → audit log records both prints. Manual print test: A4 paper output is legible at 2 m.

---

### S1-F — QR Check-Out (Week 6)

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| Backend | `POST /api/v1/visits/{id}/check-out` — sets `actual_departure=now()`, `status='checked_out'`, `badge_returned`, `ppe_issued` updates | `vms-api/app/api/v1/visits.py` | Status flips; second call returns 409 (already checked out) |
| Backend | `POST /api/v1/visits/batch-checkout` — closes all `checked_in` visits older than X hours; logs `System Batch Check-Out` reason | `vms-api/app/api/v1/visits.py` | End-of-day sweep works (requires Admin role) |
| Frontend | `CheckOutPage.tsx` — desktop: input field for scanned QR (USB scanner sends to keyboard) + "Lookup" button → fetches visit by UUID → shows confirmation card with badge / PPE returned checkboxes | `vms/src/pages/CheckOutPage.tsx` | Scanner emulation typing UUID + Enter finds the visit |
| Frontend | `CheckOutPage.tsx` — mobile: opens camera via `getUserMedia` API + decodes QR with `qr-scanner` or `jsqr` library; auto-popup on detection | `vms/src/pages/CheckOutPage.tsx`, `vms/src/components/QrScanner.tsx` | iOS Safari 17 + Chrome Mobile work on localhost over HTTPS (use mkcert in dev) |
| Frontend | `CheckOutConfirm` dialog: badge returned ✓ + PPE returned ✓ + notes → submits to check-out API | `vms/src/components/CheckOutConfirm.tsx` | Submitting flips status |
| Backend | `vms-api`: dashboard endpoint `GET /api/v1/dashboard/overview` — on-site count, today's appointments, overdue count | `vms-api/app/api/v1/dashboard.py` | Returns counts; reflects current state |
| Frontend | `VisitListPage` — "On-Site Now" tab uses `/visits/active` + duration-on-site calc | `vms/src/pages/VisitListPage.tsx` | Tab shows live count + sortable stay duration |

**Acceptance:** A visit goes: Create → Print (check-in) → Mobile scan QR (check-out). Total elapsed time in the UI ≤ 60 s. Audit log shows the trail.

---

### S1-G — Audit Logs + Basic Dashboard (Week 7)

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| Backend | `GET /api/v1/audit-logs` — filter by user_id, action_type, entity_type, from/to date; auditor + admin only | `vms-api/app/api/v1/audit.py` | PRD §6.5.5 endpoints return scoped data |
| Backend | `GET /api/v1/audit-logs/export` — CSV stream of filtered audit logs | `vms-api/app/api/v1/audit.py` | Excel opens the CSV cleanly |
| Frontend | `DashboardPage.tsx` — cards: On-site now, Today's check-ins, This week's visits, Overdue; tabular bottom: today's appointments | `vms/src/pages/DashboardPage.tsx` | Cards render real counts from `/dashboard/overview` |
| Frontend | `AuditLogPage.tsx` — filter form + paged table; admin/auditor only (role guard) | `vms/src/pages/AuditLogPage.tsx` | Non-audit role sees "Forbidden" message |
| Backend | Verify VMS-AU-013 e-signature shape (binary blob or base64 text in `vms_health_declarations.signature`) and define column type | `vms-api/alembic/versions/0003_signature_column.py` (if change needed) | Column accepts ~50 KB base64 signature data |
| Frontend | `VisitorSearch` improvements: returning visitor lookup pre-fills form | `vms/src/components/VisitorSearch.tsx` | Frequent visitor flow saves typing |

**Acceptance:** Auditor logs into VMS, exports the past week's audit log as CSV, opens in Excel — all rows readable.

---

### S1-H — S1 Integration + Pilot Hardening (Week 8)

| Task | Detail |
|------|--------|
| End-to-end E2E test | playwright script: login at Portal → land on VMS → create visit → print badge → check status checked_in → scan QR (simulated) → check status checked_out |
| Browser print check | Chrome / Edge / Firefox / Safari on Windows print A4 badge — visual diff against design mockup |
| Mobile QR camera | iOS Safari 17 + Android Chrome 90+ — scan UUID QR code on test badge |
| `./check-health.sh` extension | Adds vms-api `/health` and vms-frontend root probe |
| Pilot setup | Create 2–3 Host accounts (existing requester users), seed 5 dummy visitors, run a 2-day shadow alongside paper register; collect feedback |
| Performance smoke | List 1,000 visits — page renders < 2 s; create + print round-trip < 3 s |
| Bug fix bucket | Reserve ~3 days for fixes uncovered during E2E + pilot |

**S1 Acceptance (Phase 1 Done When):**
- [x] Portal → VMS card → SSO works
- [x] Any UniOps role can create a visit appointment
- [x] Browser badge print = check-in (atomic), reprint does not re-check-in
- [x] Mobile QR check-out works on iOS + Android  ⚠ *code complete; physical-device verification deferred to pilot*
- [x] Audit log captures every mutation; export to CSV
- [x] On-site dashboard shows real counts in real time
- [x] `docker compose up` from scratch → all services healthy in < 10 minutes (TTHW)

---

## S1 — Phase 1 Ship Summary (2026-05-30)

### Backend deliverables (vms-api)

| Layer | Module / files | Lines |
|---|---|---|
| Scaffold | `Dockerfile` · `pyproject.toml` · `requirements.txt` · `alembic.ini` · `alembic/env.py` · `app/main.py` · `app/core/{config,deps,security,request_meta}.py` · `app/db/{base,session}.py` · `app/api/v1/health.py` | ~480 |
| Models | `app/models/{visitor,visit,badge_print,health_declaration,audit_log,vms_config,user_mirror}.py` (6 owned + 1 read-only mirror) | ~290 |
| Schemas | `app/schemas/{visitor,visit,badge_print,health_declaration,audit_log,vms_config}.py` | ~230 |
| Migrations | `0001_vms_initial.py` (5 ENUMs + 6 tables + singleton seed) · `0002_audit_immutable.py` (REVOKE UPDATE/DELETE on `vms_audit_logs`) | ~270 |
| CRUD | `app/crud/{visitor,visit,badge,audit,audit_query}.py` | ~530 |
| API | `app/api/v1/{visitors,visits,badge,dashboard,audit}.py` | ~720 |
| Cross-service | `epms-api` new `GET /api/v1/users/directory[/{id}]` (public, replaces admin-only `/users`) | +90 |
| Infra | `docker-compose.dev.yml` (vms-api :8008, vms-frontend :5176, `vms_node_modules` volume, CORS 5176 across 6 backends) · `check-health.sh` extended | +60 |

### Frontend deliverables (vms/)

| Layer | Files | Lines |
|---|---|---|
| Scaffold | `package.json` · `vite.config.ts` · `tsconfig.{app,node,}.json` · `eslint.config.js` · `index.html` · `.env.example` · `.gitignore` | ~120 |
| Auth + lib | `src/main.tsx` (Portal `#__session=` handoff) · `src/store/auth.ts` · `src/lib/{api,signOut,utils}.ts` | ~210 |
| Layout | `src/components/layout/AppLayout.tsx` (sidebar + collapse + role-gated `Compliance` section + `no-print` for badge view) | ~270 |
| Components | `BadgePreview.tsx` · `QrScanner.tsx` · `CheckOutConfirm.tsx` · `VisitorSearch.tsx` · `HostSearch.tsx` · `StatusBadge.tsx` | ~700 |
| Pages | `DashboardPage` · `VisitListPage` · `ActiveVisitsPage` · `VisitCreatePage` · `VisitDetailPage` · `BadgePrintPage` · `CheckOutPage` · `AuditLogPage` | ~1,290 |
| Services | `src/services/api.ts` (typed React Query hooks for all backend endpoints) | ~310 |

### Portal integration

| Change | File |
|---|---|
| `VMS_URL` constant exported | [portal/src/lib/api.ts](portal/src/lib/api.ts) |
| `UserCheck` icon imported · `NAV_SECTIONS.MODULES` adds VMS · `resolveHref('vms')` branch · `vmsHref` built with session handoff · MODULES card | [portal/src/pages/PortalHome.tsx](portal/src/pages/PortalHome.tsx) |
| docker-compose env `VITE_VMS_URL` + `VITE_VMS_API_URL` | [docker-compose.dev.yml](docker-compose.dev.yml) |

### Test coverage

| Test file | Cases | Notes |
|---|:---:|---|
| `tests/test_visitors.py` | 6 | CRUD + search + 404 + patch |
| `tests/test_visits.py` | 13 | CRUD + dept-manager visibility + auditor full visibility + cancel scope + audit log row count |
| `tests/test_badge.py` | 12 | Atomic check-in · reprint guards · template RBAC |
| `tests/test_checkout.py` | 10 | Status gates · scope · batch (Admin) · dashboard counters · overdue |
| `tests/test_audit.py` | 12 | RBAC (5 roles) · filters · pagination · CSV stream · week_count |
| `tests/test_e2e_host_flow.py` | 3 | Full Host journey + cancel path + reprint path |
| `tests/test_performance.py` | 3 | 1000-row bulk insert · list / active / dashboard < 2s |
| **Total** | **59** | **All green** |
| `epms-api/tests/test_users_directory.py` | 11 | Open-access regression (W2 contract) |

### Performance results (1000-row dataset, local PG 15)

| Endpoint | Wall-clock (median) | Budget | Margin |
|---|:---:|:---:|:---:|
| `GET /api/v1/visits?page_size=50`   | 50 ms  | 2,000 ms | 40× |
| `GET /api/v1/visits/active`         | 10 ms  | 2,000 ms | 200× |
| `GET /api/v1/dashboard/overview`    | 30 ms  | 2,000 ms | 67× |

### Architectural decisions locked

| Decision | Value |
|---|---|
| Backend port | **8008** (8007 was already taken by `budget-api`) |
| Frontend port | **5176** |
| Database schema | `public.vms_*` (no separate `vms` schema; consistent with all other UniOps services) |
| Audit log immutability | DB-level `REVOKE UPDATE, DELETE` (migration 0002) |
| User master | Shared `public.users`; any authenticated UniOps role can be a Host |
| QR encoding | Visit UUID (same payload for desktop USB scanner & mobile camera) |
| Badge layout | 140mm × 100mm, two per Letter sheet, area color band |
| Print framework | Browser native `window.print()` + `@media print` (no thermal printer driver) |
| Quality Manager (S2 prep) | VMS-local roster in `vms_config.quality_manager_user_ids` — not added to UniOps `VALID_ROLES` |

### Items deliberately deferred to Sprint 2

| Item | Reason | Lands in |
|---|---|---|
| approval-api `vms_visit` doc_type integration | Closed-loop MVP first; approval-api refactor needs a dedicated PR | S2-B (W10) |
| Health declaration + e-signature | GMP-zone gate not needed until approval is live | S2-A (W9) |
| VMS Admin panel (QM roster · health questions · notification contacts · badge template editor) | Backend stubs exist; UI ships with S2 | S2-C (W11) |
| Portal Task Inbox `vms_visit` aggregation + deep-link | Requires approval-api `_DOC_META["vms_visit"]` first | S2-D (W12) |
| CFIA / GMP audit report generators | Format spec from Compliance pending | S2-E (W11–12) |
| `file-api` attachment upload (`entity_type="vms_visit"`) | Visitor attachments not needed for MVP closed loop | S2-E (W11–12) |
| `VisitorSearch` "last visit" hint pre-fill | Nice-to-have polish | S3 / pilot iteration |
| Cross-browser print visual diff | Physical printers required | Pilot week |
| Mobile QR camera physical-device verification | Code complete; needs real iPhones / Androids in QA | Pilot week |

### Pilot rollout artifacts

| Artifact | Purpose |
|---|---|
| [vms-api/scripts/seed_pilot.py](vms-api/scripts/seed_pilot.py) | `python -m scripts.seed_pilot` (with `HOSTS=alice@... ,bob@...`) seeds 6 visitors + 8 appointments incl. checked-in + overdue. Refuses to run against `epms` DB unless `VMS_SEED_PROD=yes-im-sure` |
| [check-health.sh](check-health.sh) | Extended to probe vms-api `:8008` + vms-frontend `:5176`; surfaces "may not be running" warning for missing frontend |
| Phase 1 demo script *(suggested)* | (a) seed pilot data → (b) sign in to Portal → (c) click VMS → (d) Dashboard reads 8 today / 2 on-site / 1 overdue → (e) Print Badge for confirmed visit → (f) `/check-out` page → paste/scan visit UUID → confirm departure |

---

## S2 — Compliance (Week 9–12, Approval + Health + Reports)

> **Goal:** End of W12 — GMP-zone visits go through `dept_manager` + Quality Manager dual approval via approval-api; visitors complete a health declaration with e-signature before badge print; Auditor can generate CFIA / GMP reports. VMS tasks show in Portal Task Inbox.
>
> **Plan revised 2026-05-30 after architecture pre-flight review.** See [S2_ARCHITECTURE_REVIEW.md](docs/S2_ARCHITECTURE_REVIEW.md) for the 9 findings (F1–F9) and the 7 locked decisions. Net effect: S2-B (W10) absorbs ~50% more work (the engine `status_attr` indirection, QM roster backend pulled in, post-approve callback). S2-D (W12) Portal work shrinks because epms-api's `task_crud.get_for_role` already aggregates `vms_visit` tasks for free. S2-A's kiosk path is deferred. S2-E ships an interim CFIA format pending Compliance review.

### S2-A — Health Declaration + E-Signature (Week 9)

> **Revised per [S2_ARCHITECTURE_REVIEW.md](docs/S2_ARCHITECTURE_REVIEW.md) F7:** the public-PC kiosk signed-URL path is deferred to Phase 3 backlog. Host-fills-in-on-behalf path is what ships here. Visitor signs on the Host's tablet, not a public kiosk.

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| Backend | `POST /api/v1/visits/{id}/health-declaration` — Host fills in (authenticated); writes `vms_health_declarations` row + updates `vms_visits.health_decl_status` + `safety_training_confirmed` | `vms-api/app/api/v1/health_decl.py`, `vms-api/app/crud/health_decl.py` | `passed` / `failed` / `restricted` flag set; audit log row written |
| Backend | `GET /api/v1/visits/{id}/health-declaration` — view filed declaration (auditor read access) | `vms-api/app/api/v1/health_decl.py` | Returns questionnaire JSON + signature image data |
| Backend | Question template config: `vms_config.health_questions: JSONB` — Admin-editable per PRD §2.2.2 VMS-CI-010 | `vms-api/app/crud/config.py` | Admin patches questions; future visits use updated template; historical declarations keep their original snapshot in `questionnaire_data` |
| Backend | Print-badge guard: if `access_area in (production_gmp, laboratory)` and no `passed` health decl yet → return 422 "Health declaration required" | `vms-api/app/api/v1/badge.py` | Cannot print GMP badge without passing health decl |
| Frontend | `HealthDeclForm.tsx` — questionnaire driven by `vms_config.health_questions`; signature pad via `react-signature-canvas` (or `signature_pad`); visitor signs on Host's tablet | `vms/src/components/HealthDeclForm.tsx` | Signature captured as base64 PNG, persisted to backend, audit log records the submission |
| Frontend | `VisitDetailPage` integrates: GMP-zone visits show "Complete Health Declaration" before "Print Badge"; failed → badge area auto-downgrades to office-only and shows red banner | `vms/src/pages/VisitDetailPage.tsx` | Failed declaration → only office badge color allowed |
| ~~Frontend~~ | ~~Public-PC self-fill mode (signed link)~~ — **Deferred to Phase 3 backlog per [S2_ARCHITECTURE_REVIEW.md](docs/S2_ARCHITECTURE_REVIEW.md) F7.** Signed-URL subsystem is ~1–2 weeks of work not justified at MVP scale. Hosts handle visitor signature on their own device. | — | — |

---

### S2-B — approval-api `vms_visit` Doc Type Integration (Week 10)

> **Revised per [S2_ARCHITECTURE_REVIEW.md](docs/S2_ARCHITECTURE_REVIEW.md) F1, F2, F3, F4, F6, F9.** This is the largest single-sprint engineering item across S2. ~600 lines + ~250 lines tests vs. SPRINT v1.0's implied ~200 lines. See the review for full code sketches.

#### Migrations (run first)

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| vms-api alembic | `0003_visit_approval_state.py` — `ALTER TABLE vms_visits ADD COLUMN approval_status VARCHAR(20) NULL, ADD COLUMN visit_title VARCHAR(255) NOT NULL DEFAULT ''` (F1, F3 — *real* `visit_title` column per locked decision #2) | `vms-api/alembic/versions/` | Two new columns on `vms_visits`; existing rows backfilled with empty title |
| approval-api alembic | `ALTER TABLE tasks ALTER COLUMN document_type TYPE VARCHAR(20)` (F6) | `approval-api/alembic/versions/` | `tasks.document_type` widens from 10 → 20 chars |

#### approval-api engine extension

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| approval-api | New thin `Visit` mirror — only fields engine reads/writes (`id`, `host_id`, `created_by`, `access_area`, `visit_title`, `approval_status`, `approval_step_idx`, `submitted_at`, `quality_approver_id`); ORM points at `vms_visits` table | `approval-api/app/models/visit.py` (new) | Mirror loads via `select(Visit).where(Visit.id==...)`; writes commit cleanly |
| approval-api | `_DOC_META["vms_visit"]` entry per locked review §"Code sketches" #2 — incl. `status_attr="approval_status"`, `number_attr="visit_title"`, `valid_*` from engine's own vocabulary (`draft / submitted / in_review / approved`) | `approval-api/app/crud/engine.py` | New entry registered; `_resolve_meta("vms_visit")` returns it |
| approval-api | **`status_attr` indirection refactor (F1)** — add `_status_of(meta, doc)` / `_set_status(meta, doc, value)` helpers; replace ~20 hardcoded `doc.status` reads/writes in `execute_action()` so each doc_type's status column is configurable. Default stays `"status"` so PR/PO/PA/EXP/MIL/TRV/CFM/BudgetPlan are unaffected. | `approval-api/app/crud/engine.py` | Full regression: existing PR/PO/PA/EXP/MIL/TRV/CFM submission/approval tests still green |
| approval-api | `amount_attr`/`vendor_attr` None-safe handling in `_create_approve_task` (Visit has neither) — change `getattr(doc, meta["amount_attr"])` to `getattr(doc, meta["amount_attr"], None) if meta.get("amount_attr") else None` | `approval-api/app/crud/engine.py` | Task creation succeeds with `meta["amount_attr"]=None` |
| approval-api | QM assignment branch in `_create_approve_task` (locked review §"Code sketches" #4): when `role=="quality_manager" and doc_type=="vms_visit"`, set `assigned_user_id = doc.quality_approver_id` | `approval-api/app/crud/engine.py` | Visit with `quality_approver_id` set → task row gets `assigned_user_id` populated; surfaces in Portal via `assigned_user_id==me` branch (F5) |
| approval-api | **`_actor_can_approve` signature change (F2)** — add `doc=None` kwarg; QM auth check reads `doc.quality_approver_id`; single caller in `execute_action()` updated to pass `doc=doc` | `approval-api/app/crud/engine.py` | Calling without `doc` works for non-QM steps (back-compat); QM step authz requires actor match |
| approval-api | **`_post_approve_vms_visit` callback (F9)** — when all approval steps clear, flip the user-facing `vms_visits.status` from `pending_approval` to `confirmed` via raw SQL UPDATE (mirror doesn't see the VMS enum column) | `approval-api/app/crud/engine.py` | `_POST_APPROVE["vms_visit"]` registered; final approval sets `status='confirmed'` |
| approval-api | `_WORKFLOW_DEFAULTS["vms_visit"]` — single-step `dept_manager` default; admin adds `quality_manager` step via Portal workflow editor | `approval-api/app/crud/engine.py` | `seed_default_workflows()` lifecycle hook populates `workflow_defs["vms_visit"]` on first startup |
| approval-api | `_DOC_TYPES = ("pr", "po", "pa", "vms_visit")` so Portal workflow editor (`GET /approval/v1/workflows/{doc_type}`) accepts vms_visit | `approval-api/app/api/v1/workflows.py` | `GET /workflows/vms_visit` returns config |
| approval-api | Regression tests + new vms_visit tests in `test_engine.py` | `approval-api/tests/test_engine.py` | All PR/PO/PA/EXP/MIL/TRV/CFM/BudgetPlan paths green; vms_visit submit→task, approve→post-approve callback, QM authz, return/cancel all green |

#### vms-api wiring

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| vms-api | Add `approval_status: str \| None` + `visit_title: str` to `Visit` ORM | `vms-api/app/models/visit.py` | Columns map to migration 0003 |
| vms-api | **QM roster backend pulled into W10 (F4)** — `GET /api/v1/admin/quality-managers` and `PUT /api/v1/admin/quality-managers` (Admin-only, atomic JSONB replace). Frontend UI stays in S2-C / W11. | `vms-api/app/api/v1/admin.py` (new) | Admin can `PUT` a list of UUIDs; persisted to `vms_config.quality_manager_user_ids` |
| vms-api | `app/services/approval.py` helpers (locked review §"Code sketches" #6) — `access_requires_approval(area)`, `access_requires_quality_manager(area)`, `pick_quality_manager(cfg)` (**first-active strategy** per locked decision #7), `submit_for_approval(visit, token, visitor_label)` | `vms-api/app/services/approval.py` (new) | `pick_quality_manager` returns `cfg.quality_manager_user_ids[0]` if non-empty and user is active |
| vms-api | `POST /visits` after-create hook — if `access_requires_approval`: set `visit_title` to `"VMS Visit — {first} {last} ({company})"`, set `status=pending_approval`, set `approval_status="draft"`, pick QM (if GMP/lab) → `quality_approver_id`, then call `submit_for_approval` | `vms-api/app/api/v1/visits.py` | GMP visit goes through approval; office visit confirms directly |
| vms-api | Read-side status sync helper `sync_status_from_approval(visit)` (locked review §"Code sketches" #7) — translates `approval_status="approved"` → `status="confirmed"` on `GET /visits/{id}` and list paths | `vms-api/app/crud/visit.py` | Visits in `pending_approval` whose `approval_status` is terminal get user-facing `status` mirrored on read |
| vms-api | Print-badge guard: refuse with 422 when `status == "pending_approval"` | `vms-api/app/api/v1/badge.py` | Cannot print badge before approval clears |
| vms-api | Cancel cascade: `POST /visits/{id}/cancel` calls approval-api `action=cancel` for any visit in `pending_approval` | `vms-api/app/api/v1/visits.py` | Both `Visit.status` and `Visit.approval_status` end as `cancelled` |
| vms-api | New `tests/test_approval_integration.py` — embeds approval-api ASGI app + vms-api ASGI app pointed at the same test DB; covers happy path (Office no-approval, Warehouse single-step dept_manager, GMP dual-step dept_manager + QM) + reject + cancel cascade | `vms-api/tests/test_approval_integration.py` (new) | ~250 lines, ~10–12 tests, all green |

**S2-B Acceptance:**
- Submit a GMP visit → tasks appear for both dept_manager and Quality Manager in their Portal task inboxes
- Both approve → `_post_approve_vms_visit` flips `Visit.status=confirmed` → print badge succeeds
- Either rejects or admin cancels → `Visit.status=cancelled`, no tasks left open
- Office visit still bypasses approval entirely (no engine call, no `approval_status` set)

---

### S2-C — VMS Admin Panel (Week 11)

> **Revised per [S2_ARCHITECTURE_REVIEW.md](docs/S2_ARCHITECTURE_REVIEW.md) F4:** the Quality Manager roster *backend* endpoint moved into W10 (so S2-B testing has a write path). This week is now mostly **frontend UI** plus the two admin endpoints that are NOT QM-related (notification contacts + health questions).

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| Backend | `GET / PUT /api/v1/admin/notification-contacts` — manage `vms_config.notification_contacts: JSONB` (`training_email`, `ppe_email`) | `vms-api/app/api/v1/admin.py` (extend the W10 file) | Admin can change emails per VMS-PR-023 |
| Backend | `GET / PUT /api/v1/admin/health-questions` — manage `vms_config.health_questions` template | `vms-api/app/api/v1/admin.py` | Admin updates question set; future visits use new template |
| Backend | Email dispatch helpers — on visit confirmation, if access_area triggers training → email Training Contact; if PPE needed → email PPE Contact (PRD VMS-PR-021/022). Uses shared SMTP config from `company_config`. | `vms-api/app/services/notifications.py` | Emails sent within 60s of visit confirmation |
| Frontend | `AdminPanel.tsx` shell with sub-pages: Quality Managers / Notification Contacts / Health Questions / Badge Templates | `vms/src/pages/admin/AdminPanel.tsx` | Role guard: `system_admin` only (UI side; backend already enforces) |
| Frontend | `QualityManagerRosterPage.tsx` — search user via `epms-api/users/directory`, multi-select, save. Calls the W10 backend endpoint. | `vms/src/pages/admin/QualityManagerRosterPage.tsx` | Selected users persist; show department badges; W10 integration tests can now write rosters via UI |
| Frontend | `NotificationContactsPage.tsx` — two email inputs with validation, save button | `vms/src/pages/admin/NotificationContactsPage.tsx` | Empty save warns; submit changes effective immediately |
| Frontend | `HealthQuestionsPage.tsx` — JSON editor (Monaco or simpler `react-json-view`) for question template; preview pane | `vms/src/pages/admin/HealthQuestionsPage.tsx` | Editor validates JSON before save |
| Frontend | `BadgeTemplatesPage.tsx` — HTML/CSS editor for badge templates; live preview iframe (uses the W5 stub `PUT /badge/templates/{name}` endpoint that already exists) | `vms/src/pages/admin/BadgeTemplatesPage.tsx` | Admin can author template; preview reflects edits |

---

### S2-D — Portal Task Inbox + Admin Workflow Tab (Week 12, parallel with S2-E)

> **Heavily simplified per [S2_ARCHITECTURE_REVIEW.md](docs/S2_ARCHITECTURE_REVIEW.md) F5:** Portal's existing `useEpmsTasks` already pulls every task assigned to the user (via `assigned_user_id == me OR assigned_role IN my_roles`). Because the W10 QM special-case wires `assigned_user_id = Visit.quality_approver_id`, **VMS tasks surface automatically** — no aggregation change. All that's left is the rendering label + deep-link mapping.

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| Portal | Extend `DOC_PATH` with `vms_visit: '/'` (or `'/${id}'` shape) for deep-link from inbox to VMS frontend | `portal/src/pages/PortalHome.tsx` | Click on a vms_visit task → lands on `${VITE_VMS_URL}/${visit_id}#__session=<jwt>` |
| Portal | Extend `STATUS_LABEL` with `pending_approval: "Pending Visit Approval"` (shared with VMS) and/or task-type-specific copy for `approve_vms_visit` | `portal/src/pages/PortalHome.tsx` | Status copy reads correctly in the task list rows |
| Portal | Approve / Return / Cancel buttons on inbox row → call existing `POST /approval/v1/approvals/{doc_type}/{id}/action` util (no change needed; doc_type is read from the task row) | `portal/src/lib/approval.ts` (verify util already generic) | Approve / Return / Cancel for vms_visit work from Portal without leaving |
| Portal | Admin → Approval Workflows editor: add VMS Visit tab to the existing 8-tab `ApprovalWorkflows` editor (AE-5A pattern); reuses `WorkflowDef` schema; edits `workflow_defs["vms_visit"]` | `portal/src/pages/admin/AdminPanel.tsx` | Admin can edit chain; save → next vms-api submit uses new config |
| vms-api | Approval status surfacing on read — already wired in W10 via `sync_status_from_approval` helper; nothing new here. | — | `GET /api/v1/visits/{id}` returns `status=confirmed` after both approvals clear |
| vms-api | On approval result, send email to Host (confirmation or rejection notice) — uses the notifications service introduced in S2-C | `vms-api/app/services/notifications.py` | Host gets email within ~1 minute of final approval action |

---

### S2-E — CFIA / GMP Reports + File Upload (Week 11–12, parallel)

> **Revised per [S2_ARCHITECTURE_REVIEW.md](docs/S2_ARCHITECTURE_REVIEW.md) F8:** Compliance team's final CFIA column list is not yet confirmed. We ship an **interim format** based on the PRD's natural column list, mark it as "interim — pending Compliance review" in the UI, and revise after the first real CFIA audit dry-run. **Don't wait for Compliance to unblock the sprint.**

#### Interim CFIA column list (until Compliance confirms)

```
date_of_visit | visitor_full_name | company | visitor_type
host_full_name | host_department | access_area | health_decl_status
planned_arrival | actual_arrival | actual_departure
on_site_minutes | safety_training_confirmed | badge_returned
ppe_issued | notes
```

This is the union of fields the PRD §2.5.2 audit requirements cite. Compliance will redline / cut after seeing real output.

| Layer | Task | Files | Done When | Status |
|-------|------|-------|-----------|--------|
| Backend | `GET /api/v1/reports/cfia-visit-log` — streamed CSV (F8 interim format; openpyxl not added). Excel opens cleanly | `vms-api/app/api/v1/reports.py`, `vms-api/app/services/reports.py` | File downloads with interim columns; auditor can open in Excel 2019+ | ✅ |
| Backend | `GET /api/v1/reports/gmp-area-summary` — one row per GMP/Lab area with counts (total, health-decl outcomes, after-hours, unreturned badges) | `vms-api/app/api/v1/reports.py` | Streaming CSV; per-area row for production_gmp + laboratory | ✅ |
| Backend | file-api integration — `POST /api/v1/visits/{id}/attachments` proxies to file-api with `doc_type="vms_visit"`; `GET /api/v1/visits/{id}/attachments` lists from `file_metadata` mirror | `vms-api/app/api/v1/visits.py`, `vms-api/app/services/attachments.py`, `vms-api/app/models/file_metadata_mirror.py` | Uploads visible in `file-storage/`; list returns metadata + download URLs; visibility-scoped; auditor 403 on upload | ✅ |
| Backend | `GET /api/v1/dashboard/compliance` — KPIs for Dashboard compliance card | `vms-api/app/api/v1/dashboard.py`, `vms-api/app/services/reports.py::compliance_metrics` | gmp_visits_this_month, gmp_pass_rate, unreturned_badges, after_hours_visits_today (null pass_rate when no outcomes) | ✅ |
| Frontend | `ReportsPage.tsx` — date range picker + Download CSV per report | `vms/src/pages/ReportsPage.tsx` | Auditor downloads CFIA log + GMP summary for any date range; interim-format note | ✅ |
| Frontend | `VisitDetailPage` attachment uploader + viewer | `vms/src/pages/VisitDetailPage.tsx`, `vms/src/components/VisitAttachments.tsx` | Single-file upload works; preview opens via file-api download URL | ✅ |
| Frontend | Dashboard compliance metrics card: GMP visits this month, pass rate, unreturned badges, after-hours visits | `vms/src/pages/DashboardPage.tsx` | Cards render with refresh-on-interval; Reports link | ✅ |
| Process | Schedule a 30-min review with Compliance after first real export — collect their column / format / signature-line redlines, file a Phase 3 ticket | — | Notes filed; "Interim format" banner stays until Compliance signs off | 📋 |

**S2 Acceptance (Phase 2 Done When):**
- [ ] GMP-zone visit needs dept_manager + Quality Manager dual approval before badge prints
- [ ] Approval tasks appear in Portal Task Inbox alongside PR/PO/PA — same UI, same endpoints
- [ ] Portal Admin Workflow Defs editor has a VMS Visit tab; edits take effect on next submit
- [ ] Health declaration with e-signature is required before GMP badge; failed → office-only restriction
- [x] CFIA / GMP audit reports generate as CSV (interim format per F8; opens cleanly in Excel)
- [ ] Training Contact / PPE Contact email notifications fire correctly
- [x] Visitor attachments stored in file-api with `doc_type="vms_visit"`

---

## S3 — Polish (Week 13–14, Alerts + Batch + Export)

### S3-A — Overtime Alerts + Notifications (Week 13)

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| Backend | Scheduled job (APScheduler or PG cron pattern via `pg_cron` if available; else simple loop in vms-api startup) — every 5 min scan `vms_visits` where `status=checked_in` and `planned_departure < now()` | `vms-api/app/services/overdue_scanner.py` | Overdue visits get `overdue=True` flag |
| Backend | 1 h overdue → email Host; 4 h overdue or past business hours → email Department Manager (escalation per PRD VMS-CO-009/010/011) | `vms-api/app/services/notifications.py` | Emails sent once per escalation tier (idempotent by stored `last_notified_at`) |
| Backend | Appointment reminders: T-1 day to Host (VMS-PR-012); confirmation email to visitor + Host on appointment creation (VMS-PR-011) | `vms-api/app/services/notifications.py` | Cron job emits reminders within 5 min of trigger time |
| Frontend | Dashboard "Overdue" card highlights count + click-through to filtered list | `vms/src/pages/DashboardPage.tsx` | Overdue badges show on visit rows |
| Backend | `notification_channel` respected from `public.users` — email_only delivers via SMTP; teams_only / both deferred to Phase 3 (document explicitly) | `vms-api/app/services/notifications.py` | email_only users get emails; teams_only logged as skipped |

---

### S3-B — Batch Operations + Export + Pilot Rollout (Week 14)

| Layer | Task | Files | Done When |
|-------|------|-------|----------|
| Backend | `POST /api/v1/visits/batch-checkout` — closes all on-site visits in one call; idempotent; audit log records reason | `vms-api/app/api/v1/visits.py` | End-of-day sweep works; Admin-only |
| Backend | `POST /api/v1/visits/batch-import` — Excel template (visitor + visit columns) bulk creates appointments per VMS-PR-010 | `vms-api/app/api/v1/visits.py` | 100-row Excel imports < 30 s; validation errors reported per row |
| Backend | "Copy last appointment" endpoint helper: `GET /api/v1/visits/recent-by-visitor/{visitor_id}` for one-click reuse (VMS-PR-009) | `vms-api/app/api/v1/visits.py` | Frontend can pre-fill from latest visit |
| Frontend | `BatchImportPage.tsx` — Excel upload + preview + commit | `vms/src/pages/BatchImportPage.tsx` | Errors highlighted in preview before commit |
| Frontend | `VisitListPage` — bulk-select + "Batch Check-Out" button (Admin only) | `vms/src/pages/VisitListPage.tsx` | Confirmation modal; check-out cascades |
| Frontend | `VisitDetailPage` — "Copy Appointment" button creates a draft from this visit's data | `vms/src/pages/VisitDetailPage.tsx` | Form opens pre-filled |
| Frontend | Audit log export: PDF format option (in addition to CSV) | `vms/src/pages/AuditLogPage.tsx`, `vms-api/app/api/v1/audit.py` | PDF download works |
| Pilot | Pilot rollout: 2 weeks of parallel run alongside paper register; feedback collection; bug-fix bucket | — | Sign-off from Administration + Compliance |

**S3 Acceptance (Phase 3 Done When):**
- [ ] Overtime alerts emit at 1 h / 4 h tiers; idempotent across scanner restarts
- [ ] Appointment reminders (T-1 day) and confirmation emails delivered
- [ ] Excel batch import handles 100-row file with per-row error reporting
- [ ] Admin can batch check-out at end of day with one confirmation
- [ ] Pilot sign-off from at least one Host + Administration + Compliance representative

---

## Service Map (Post-S3)

```
Local Dev (docker-compose.dev.yml)            Production (distributed)
─────────────────────────────────             ──────────────────────────────────
portal      :5174                              Web Server
epms        :5173                                ├─ Nginx reverse proxy
oa          :5175                                ├─ portal + epms + oa + vms (built static)
vms         :5176  ← NEW                         └─ TLS / domain
                                               App Server 1
epms-api    :8000                                ├─ epms-api :8000
approval-api:8003  (extended w/ vms_visit)       ├─ approval-api :8003
mdm-api     :8002                                ├─ mdm-api     :8002
finance-api :8004                                └─ finance-api :8004
                                               App Server 2
file-api    :8005  (entity_type=vms_visit)       ├─ expense-api :8006
expense-api :8006                                ├─ budget-api  :8007
budget-api  :8007                                └─ vms-api     :8008  ← NEW
vms-api     :8008  ← NEW                       File Server
                                                 └─ file-api :8005 + /file-storage/
postgres    :5432                              Database Server
redis       :6379                                ├─ PostgreSQL :5432 (private)
                                                 └─ Redis :6379 (private)
```

### Action Key Coverage (after S2)

| Action Key | Source Service | Doc Type | Added in |
|------------|---------------|----------|:--------:|
| `pr` | epms-api | Purchase Request | EPMS |
| `po` | epms-api | Purchase Order | EPMS |
| `pa` | expense-api | PA (PO-linked) | OA S1 |
| `pa_dir` | expense-api | PA (Direct) | OA S3 / AE |
| `exp` | expense-api | General Expense | AE |
| `mil` | expense-api | Mileage Claim | AE |
| `trv` | expense-api | Travel Expense | AE / OA S4 |
| `cfm` / `cfm_*` | expense-api | Custom Forms | OA S4 |
| **`vms_visit`** | **vms-api** | **Visitor Access** | **VMS S2** |

---

## Risk Register

| Risk | Probability | Impact | Mitigation | Status |
|------|:----------:|:------:|------------|:------:|
| `epms-api /users/directory` endpoint design contention with epms team (auth scope, payload shape) | M | H | Get sign-off from epms-api owner in W1 before VMS S1-D depends on it | 🔲 Confirm by W2 |
| Cross-service CORS additions broken by unrelated deploys | M | M | Codify 5176 in `epms-api/app/core/config.py` defaults (not just docker env) so CI / local dev both pick it up | 🔲 |
| approval-api thin `Visit` mirror drifts from vms-api full `Visit` model | M | M | Pin the shared field contract in `vms-api/app/models/visit.py` docstring; pytest in approval-api asserts ORM column compatibility | 🔲 S2-B |
| Quality Manager roster + round-robin policy unclear (single approver vs any-can-approve) | H | M | Confirm policy with QA in W1; documented in PRD §11.4 #8; default to "first active QM in roster" for MVP | 🔲 By S2-B start |
| Browser `getUserMedia` requires HTTPS — localhost has dev-cert workaround but pilot may need real cert | M | M | Use `mkcert` locally; for pilot, deploy to staging behind Nginx with self-signed or company CA | 🔲 S1-F |
| Audit log DB-level immutability (`REVOKE`) breaks dev `reset-db.sh` workflow | L | M | Run REVOKE in a dedicated production-only migration; dev keeps writes editable behind feature flag | 🔲 |
| Health declaration question template changes mid-flight — historical declarations need template snapshot | M | M | Store full questionnaire text in `vms_health_declarations.questionnaire_data` JSONB at submit time (not just refs); already designed in PRD §5.2 | ✅ Design covers it |
| Teams notification deferred to Phase 3 — some users have `notification_channel=teams_only` | L | L | Document explicitly; teams_only users get email fallback in S3 (override) | 🔲 S3-A |
| Portal Task Inbox shows hundreds of vms_visit tasks for active QMs | L | M | Same pagination as PR/PO inbox; default filter to "this week" | 🔲 S2-D |
| Production deploy: 8008 conflict with existing services on App Server 2 | L | H | Confirm 8008 free on target hosts before deploy; otherwise relocate to 8009 | 🔲 Pre-prod |

---

## Pre-Launch Checklist

### 🔲 S1 (MVP) Ready
- [ ] `docker compose -f docker-compose.dev.yml up` boots all services including vms-api + vms-frontend; healthchecks green
- [ ] `./check-health.sh` extended to include vms-api
- [ ] Portal → click VMS card → SSO into vms frontend works without re-auth
- [ ] Any UniOps role (requester, dept_manager, gm, opm, finance_bp, cfo, …) can create a visit
- [ ] Browser badge print on Chrome / Edge / Firefox / Safari produces A4 output legible at 2 m
- [ ] Mobile QR check-out works on iOS Safari 17 + Android Chrome 90+
- [ ] Print-badge atomic with check-in; reprint inserts a `vms_badge_prints` row without re-checking-in
- [ ] Audit log captures every mutation with old/new JSON, IP, user agent
- [ ] Pilot: 2 Hosts × 5 visitors over 2 days with no functional blockers

### 🔲 S2 (Compliance) Ready
- [ ] approval-api `_DOC_META["vms_visit"]` registered; `_DOC_TYPES` lists `vms_visit`; `_WORKFLOW_DEFAULTS["vms_visit"]` seeded on startup
- [ ] approval-api Quality Manager special-case resolution verified via pytest
- [ ] GMP-zone visit → `pending_approval` → tasks created for dept_manager and (if configured) quality_manager → both approve → `confirmed` → badge prints
- [ ] Portal Task Inbox shows vms_visit tasks with visitor-name labels and deep-link to vms frontend
- [ ] Portal Admin → Approval Workflows → VMS Visit tab edits `workflow_defs["vms_visit"]`; next submit uses updated chain
- [ ] VMS Admin → Quality Manager roster page: add / remove users; reflects in approval routing within the same session
- [ ] VMS Admin → Notification Contacts: Training Contact + PPE Contact emails persist; auto-notifications fire on appointment requiring training / PPE
- [ ] Health declaration with e-signature stored as base64; failed declaration auto-downgrades badge zone to office
- [ ] CFIA visit log report generates as PDF + Excel; opens cleanly in Excel
- [ ] Visitor attachments uploaded via file-api `entity_type="vms_visit"`

### 🔲 S3 (Polish) Ready
- [ ] Overdue scanner runs every 5 min; 1 h Host email + 4 h Manager escalation fire once per tier (idempotent)
- [ ] T-1 day appointment reminders deliver
- [ ] Excel batch import of 100 visits completes in < 30 s with per-row validation
- [ ] Admin batch check-out closes all on-site visits in one transaction with audit reason
- [ ] Compliance sign-off: pilot review meeting with Administration + QA + IT
- [ ] Phase 3 deferred items documented (Teams notifications, French interface, access control system integration)

---

## Out-of-Sprint Backlog (Phase 4+, post-launch)

| Item | Reason for deferral | Estimate |
|------|--------------------|:--------:|
| French interface (Bill 96 — if Quebec deployment) | Not currently in pilot scope | 2 w |
| Teams notification channel for VMS (per `notification_channel`) | Email-only sufficient for pilot | 1 w |
| Physical access card system integration | Vendor / API not yet selected | 4 w |
| Self-service check-out kiosk | Hardware procurement gate | 3 w |
| Visit-trend analytics + charts | Nice-to-have, not in core PRD | 2 w |
| Badge thermal printer support (optional escape hatch) | Browser print suffices today | 1 w |
| Multi-tenant support (multiple sites) | Single-site rollout only | 6 w |

---

*Authoring note: this sprint plan derives from VMS PRD V2.3 §9 implementation roadmap. The PRD is the source of truth for requirement IDs (VMS-PR-*, VMS-CI-*, VMS-LB-*, VMS-CO-*, VMS-AU-*); this document expands them into weekly tasks with file paths and acceptance criteria. Status updates land here as work progresses; PRD edits are reserved for scope changes.*
