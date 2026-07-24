# PMS Attachment Import (PR / PO / PA) — Design

**Date:** 2026-07-24
**Branch:** `feature/pms-attachment-import` · worktree `c:/Project/uniops-pms-attach` (base `main` @ e4b5a3e)
**Status:** Approved — ready for implementation plan

## Problem

The legacy SharePoint PMS import (EPMS → Admin Panel → **PMS Data Import**) already
migrates PR / PO / PA / Invoice **headers, line items, and INVOICE attachments**. But
the **PR / PO / PA documents' own SharePoint list-item attachments** (the files under
each item's *Attachment* column) were never imported. Users want those files brought
into EPMS so they hang off the migrated PR / PO / PA records like any manually-uploaded
attachment.

## Scope

- **In:** attachments of the three **official** SharePoint lists — `Purchase Request`,
  `PO List`, `Payment Request` — imported into EPMS `pr_attachments` / `po_attachments`
  / `pa_attachments`.
- **Out:** the `*Backup` archive lists (`Purchase Request Backup`, `PO List Backup`,
  `Payment Request Backup`) — never touched. INVOICE attachments already handled by the
  existing flow — not re-run here.
- **Trigger:** a new, **independent** button **Import Attachments** on the existing PMS
  Data Import panel. It does **not** re-run header/line-item load and does **not**
  advance the incremental watermark.
- **Mode:** **full + idempotent** only (no incremental / `Modified` filter). Attachments
  rarely change; re-clicking is safe and cheap (dedup by `(doc_id, filename)`).
- Reuses the existing **dry-run** checkbox for preview.

## Design

### 1. Extract (`scripts/import_pms/extract.py`)

Generalize the existing `extract_invoice_attachments` into a reusable helper:

```
extract_list_attachments(sp, list_title, number_field, subdir, meta_name, since=None) -> int
```

- Calls `sp.get_list_items(list_title, select=[number_field, "Attachments"],
  expand=["AttachmentFiles"], since=since)`.
- For each item with `Attachments == True`, downloads every `AttachmentFiles` entry to
  `data/<subdir>/<sp_item_id>/<file>` and appends a meta row
  `{doc_number, file_name, content_type, size}` where `doc_number = item[number_field]`.
- Writes `data/<meta_name>`.

Config for the three official lists (Backup lists deliberately absent):

| kind | list_title         | number_field   | subdir           | meta_name              |
|------|--------------------|----------------|------------------|------------------------|
| pr   | `Purchase Request` | `PR_x0020_No`  | `pr_attachments` | `pr_attachments.json`  |
| po   | `PO List`          | `Title`        | `po_attachments` | `po_attachments.json`  |
| pa   | `Payment Request`  | `Title`        | `pa_attachments` | `pa_attachments.json`  |

`extract_invoice_attachments` is refactored to call `extract_list_attachments` (keeps
its `sp_invoice_id` meta shape for backward compat) or left as-is; the new helper is
additive and must not change invoice behavior.

### 2. Load (`scripts/import_pms/attachments.py`)

New `sync_doc_attachments(kind, dry_run=True, db_url=None) -> AttachReport` mirroring
`sync_invoice_attachments`, with `kind ∈ {pr, po, pa}` mapped to:

| kind | att_table        | fk_col  | doc_table              | number_col  | doc_type |
|------|------------------|---------|------------------------|-------------|----------|
| pr   | `pr_attachments` | `pr_id` | `purchase_requests`    | `number`    | `"pr"`   |
| po   | `po_attachments` | `po_id` | `purchase_orders`      | `number`    | `"po"`   |
| pa   | `pa_attachments` | `pa_id` | `payment_applications` | `pa_number` | `"pa"`   |

Steps:
1. Read `data/<meta_name>`; return an empty report if absent/empty.
2. Build `doc_number → doc_id` from `select <number_col>, id from <doc_table>`.
   Unresolved numbers → the report's "not found" counter and skip.
3. Idempotency: load existing `(<fk_col>, filename)` from `<att_table>`; a match →
   `skipped_existing`.
4. On dry-run: count would-be uploads, write nothing.
5. On commit: read the staged file; upload via
   `app.services.attachment_helper.upload_to_file_server(data, filename, content_type,
   doc_type, doc_id, token)` — `service="epms"`, identical to the PR/PO/PA manual-upload
   endpoints, so the file server records the correct owner. Then
   `insert into <att_table>(id, <fk_col>, filename, content_type, file_size,
   storage_key)`; `created_at/updated_at` fill from `server_default=func.now()`.
6. Token: `create_access_token(subject=str(sysid), role="system_admin")`, same system
   user (`migration@epms.local`) as invoice attachments.

**Report:** reuse the existing `AttachReport` class **unchanged** (invoice flow keeps
using it as-is). `sync_doc_attachments` returns an `AttachReport` too; when the runner
serializes it for the doc-attachments block it exposes the "not found" count under the
neutral key `no_doc` (key rename happens in the runner, not in `AttachReport.to_dict`,
so `sync_invoice_attachments`'s `no_invoice` output is untouched). Fields surfaced:
`uploaded / skipped_existing / no_doc / failed / samples_failed`.

### 3. Runner (`app/services/pms_import_runner.py`)

`_execute` gains a `phase == "attachments"` branch that:
- Ensures SP creds (same guard as today).
- Runs `extract_list_attachments` for pr/po/pa (full; `since=None`) off the event loop
  via `asyncio.to_thread`.
- Calls `sync_doc_attachments('pr'/'po'/'pa', dry_run=run.dry_run)` in sequence.
- Aggregates results into `run.report = {"doc_attachments": {"pr": …, "po": …,
  "pa": …}}`.
- Does **not** touch main data or the watermark.

`start_run` already validates phase against a set — extend it to accept `"attachments"`.

### 4. API (`app/api/v1/pms_import.py`)

`RunRequest.phase: Literal["full", "incremental", "attachments"]`. No other change;
still gated by `admin_panel`.

### 5. Frontend (`epms/src/pages/admin/PmsImportPanel.tsx`, `services/pmsImport.ts`)

- `PmsRun['phase']` and the run request type gain `'attachments'`.
- `PmsReport` gains
  `doc_attachments?: { pr: AttSummary; po: AttSummary; pa: AttSummary }` where
  `AttSummary = { uploaded; skipped_existing; no_doc; failed }`.
- New third button **Import Attachments** (Paperclip icon), `onClick={() =>
  start('attachments')}`, disabled under `running || busy || noSp` and honoring the
  dry-run checkbox. Placed alongside Full import / Incremental sync.
- `ReportView` renders a "Document attachments" block showing pr/po/pa counts when
  `r.doc_attachments` is present (mirrors the existing invoice-attachments line).
- The current-run/history labels map `'attachments'` → "Import Attachments".

## Edge cases & guardrails

- **Backup lists**: never enumerated — the extract spec only lists the three official
  titles.
- **Doc not found** (SP number has no EPMS record, e.g. a PA skipped for missing PO):
  counted in `no_doc`, surfaced in the report, not fatal.
- **Missing staged file** on commit: `failed` + sample, continue.
- **Idempotent re-run**: second click uploads nothing new (all `skipped_existing`).
- **file server ownership**: `service="epms"`, `doc_type` = pr/po/pa — same as manual
  uploads, so downloads via the existing `/attachments/{id}/download` proxy just work.
- **Number field quirk**: PR uses `PR_x0020_No` (not `Title`); PO/PA use `Title`. Matches
  how `load.py` derives each document number.

## Testing

- Unit: `sync_doc_attachments` dry-run counts (uploaded/skipped_existing/no_doc) against
  a seeded local test DB (epms_test) with stub meta files — no SharePoint, no file
  server (patch `upload_to_file_server`). One test per kind + an idempotency re-run test.
- Extract: `extract_list_attachments` against a fake `SharePointClient` returning canned
  items/`AttachmentFiles`, asserting staged paths + meta rows and that Backup titles are
  never requested.
- Follow project test-DB discipline: run the epms suite serially; cover the success path
  in a smoke check.

## Release notes

- **No DB migration** — `pr/po/pa_attachments` tables already exist.
- Single-session discipline: all work stays on `feature/pms-attachment-import` in the
  isolated worktree; merge to main only at the release convergence point; build all 15
  images at one sha.
- Prod trigger is manual via the panel; a committed run writes real files to the file
  server — dry-run first.
