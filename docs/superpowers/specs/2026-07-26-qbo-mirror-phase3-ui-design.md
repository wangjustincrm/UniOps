# QBO Mirror — Phase 3 (finance-api endpoints + Finance UI) Design

**Date:** 2026-07-26
**Branch:** `feature/qbo-mirror` (worktree `c:/Project/uniops-qbo`)
**Status:** Design approved, ready for implementation plan
**Builds on:** Phase 1 (`2026-07-24-qbo-mirror-design.md`, AP core engine) + Phase 2
(`2026-07-25-qbo-mirror-phase2.md`, AR/long-tail/attachments). The full mirror
(23 entities, migrations 0027+0028) is complete and tested; this phase exposes it.

## Goal

Give Finance users a UI to trigger QBO syncs and browse the mirrored data —
without leaving UniOps and without SQL. Read-only over the mirror; still no
association with NC65/finance AP.

## Approved decisions

1. **UI scope = all transaction entities** as browse tabs: Bill, BillPayment,
   VendorCredit, Purchase, Invoice, Payment, CreditMemo, Deposit, Transfer,
   JournalEntry — plus Vendor and Account as reference tabs, plus an Attachments
   tab. (Long-tail `qbo_raw` masters are not given dedicated browse tabs.)
2. **Data sequencing = UI first, deploy after.** Build and verify against a dev
   import; deploying migrations 0027+0028 to production and running the real full
   import is a separate follow-up step (not part of this phase's rollout).
3. **Permissions = reuse `view_finance`** for both viewing and triggering sync
   (matches the Access Control Matrix pattern used by every other finance nav item).
4. **Background execution mirrors `nc_sync`**: trigger inserts the running row
   synchronously, then the async orchestrator runs on a worker thread with its own
   engine/session so it never blocks the request or the event loop.

## Architecture

Backend: one new router `finance-api/app/api/v1/qbo.py`, modeled on the existing
`nc_sync.py`. Frontend: one new page `finance/src/pages/finance/QboMirrorPage.tsx`
+ a `qboApi.ts` service, wired into the finance nav. No new services or images
beyond finance-api + finance-web.

## Backend API (finance-api)

All endpoints gated on `view_finance` (via the existing `CurrentUser` dep +
permission check used by other finance routers). Router prefix `/qbo`.

- `POST /qbo/sync` → 202. Body `{mode: "full"|"incremental", entities?: [str],
  with_attachments?: bool=true}`. Full mode requires a confirm token (like
  nc_sync's `FULL_CONFIRM`). Gates on `view_finance`; 409 if a run is already
  RUNNING; 503 if `qbo_conn.env` is absent. Inserts the `QboSyncRun` row
  synchronously, then dispatches the async orchestrator on a worker thread
  (`loop.run_in_executor` → `asyncio.run(run_sync(own_session, QboClient(), ...))`
  with a thread-local async engine). Returns `{run_id}`.
- `GET /qbo/sync/status` → sweeps stale RUNNING rows to `failed` (like nc_sync),
  returns `{can_sync, configured, current_run, last_run}` with runs serialized
  (id, mode, status, started_at, finished_at, counters, watermarks, error).
- `GET /qbo/sync/runs?limit=` → recent run history.
- `GET /qbo/{entity}?page=&page_size=&date_from=&date_to=&q=` → paged browse.
  `entity` is validated against a whitelist mapping name → model (the 12 typed
  entities). Excludes `deleted_at IS NOT NULL` by default. `q` matches
  counterparty_name / doc_number. Returns `{items, total, page, page_size}`.
- `GET /qbo/{entity}/{qbo_id}` → detail: header row + its lines (for entities
  with a line table) + reconciliation links (lines' linked_txn_*) + attachment
  links referencing this txn.
- `GET /qbo/attachments/{qbo_id}/file` → streams the `content` BYTEA with the
  stored `content_type` and `file_name` (Content-Disposition).

Entity whitelist (name → model): vendors, accounts, bills, bill-payments,
vendor-credits, purchases, invoices, payments, credit-memos, deposits, transfers,
journal-entries. A raw/long-tail browse is out of scope for this phase.

## Frontend (finance)

- **Nav:** add a `QuickBooks` item at `/finance/qbo` to the finance section in
  `AppLayout` (`permission: 'view_finance'`), with a Lucide icon.
- **Page `QboMirrorPage.tsx`** (wrapped in the finance app layout like peers):
  - **Sync panel** (top): Full / Incremental buttons; Full opens a confirm
    dialog. Shows last-sync time + status. While a run is RUNNING, poll
    `GET /qbo/sync/status` every ~2s and render a progress line with per-entity
    counters; stop polling when it leaves RUNNING. Disable triggers while running.
  - **Entity tabs:** Bill, BillPayment, VendorCredit, Purchase, Invoice, Payment,
    CreditMemo, Deposit, Transfer, JournalEntry, Vendor, Account, Attachments.
    Each data tab = a paged table with date-range + text (`q`) filters. Columns
    per entity (date, doc#, counterparty, currency, total, home total, balance …);
    amounts show currency and, when currency ≠ CAD, the home (CAD) amount.
  - **Detail modal:** row click → header fields + a lines sub-table (account,
    amount, posting type, tax, linked txn) + attachment download links.
  - **Attachments tab:** list file_name / content_type / size / linked txns, with
    a download button hitting the file endpoint.
- **Service `qboApi.ts`:** absolute `VITE_*` base URL + shared api client (never
  relative fetch). Typed responses.
- **Conventions:** `Decimal` JSON strings coerced via `Number()` before
  `.toFixed()`; app-level `StatusBadge`; custom dropdowns/popovers `createPortal`
  to body with fixed positioning; all user-facing strings English.

## Testing

- **Backend** (`tests/test_qbo_api.py`): seed a few `qbo_*` rows in finance_test,
  then assert: trigger gating (non-permitted → 403; full without confirm → 422;
  already-running → 409), status shape, paged browse with filters + soft-delete
  exclusion, detail assembly (header+lines+links), attachment file stream
  (content-type + bytes). The orchestrator dispatch is monkeypatched in the
  trigger test so no real QBO/thread runs.
- **Frontend:** `tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
  clean; a lightweight render/smoke of the page against a mocked api.

## Rollout

- Rebuild **finance-web + finance-api** images (not all 15).
- **Deploy of migrations 0027+0028 and the real production full import is a
  SEPARATE follow-up** (per the UI-first decision) — this phase ships the UI.
- **Dev:** run a one-off dev import (read-only QBO → finance dev DB) so the UI
  shows real-shaped data during build/verify.
- Branch `feature/qbo-mirror`; R1–R5 discipline; no push/merge without user consent.

## Out of scope

- Association/reconciliation between QBO data and NC65 / finance AP.
- Editing QBO data from the UI (read-only mirror).
- Browse tabs for the 11 long-tail `qbo_raw` masters.
- Production deployment + the real full import (separate follow-up).
