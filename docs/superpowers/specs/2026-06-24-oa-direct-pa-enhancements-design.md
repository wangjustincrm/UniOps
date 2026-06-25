# OA Direct-PA enhancements

**Date:** 2026-06-24
**Status:** Approved, pending implementation
**Scope:** OA module — `new Direct-PA` flow, PA detail page, and a new PA edit page. Frontend (`oa`) + backend (`expense-api`).

## Goal

Four refinements to the OA Direct Payment Application feature:

1. Use the **matched DB vendor** name (not the AI-extracted name) for the PA Title and Vendor Name.
2. Filter the **Cost Center** picker by the logged-in user's department.
3. Allow **editing a Draft / Returned** Direct PA (no edit path exists today).
4. Allow **uploading additional attachments** from the PA detail page, fix the
   broken attachment download, and keep all attachment binaries on the File Server.

## Context (current state)

- `oa/src/pages/pa/PaDirectCreatePage.tsx` — 3-step wizard (Upload → Review → Payment Details).
  - Step 2 (`Step2Review`) resolves a matched DB vendor (`selectedVendor` with `id`,`name`,`code`) but on confirm passes only `vendorId` to Step 3.
  - Step 3 (`Step3PaForm`) seeds Title and a free-text Vendor Name input from `fields.vendorName` (AI-extracted). It fetches all cost centers via `epmsApi.get('/api/v1/cost-centers?active_only=true')`. It already has an "Additional Attachments" picker, but uploads them with a raw **relative** `fetch('/api/v1/invoice-attachments?...')` (hits OA's dead Vite proxy in Docker — see project gotcha; OA must use the absolute api client).
- `oa/src/pages/pa/PaDetailPage.tsx` — detail with Details/Attachments/History tabs. Attachments tab is **read-only** (lists + a download `<a href>`). Draft/Returned owners see only Submit/Cancel.
- `expense-api/app/api/v1/pa.py` — has create (`POST /pa/direct`), get, permissions, history, action, pay. **No update endpoint.**
- `expense-api/app/schemas/pa.py` — `PaResponse` exposes `budget_account_code` but **not** `cost_center_id`. `PaDirectCreate` exists; no update schema.
- `expense-api/app/crud/pa.py` — `list_pas`, `get_by_id`, `get_by_po_id`; **no update**.
- `expense-api/app/models/pa.py` — `PaymentApplication` has `cost_center_id` (nullable).
- `expense-api/app/api/v1/invoice_attachments.py` — `POST` (upload to file-api, store `storage_key`), `GET` (list), `GET /{id}/file` (auth-required proxy download), `DELETE`.
- `expense-api/app/models/invoice_attachment.py` — stores only metadata + `storage_key` (file-api UUID). **No binary column** — the "binaries on File Server, DB only the link" rule is already met.
- epms `/api/v1/cost-centers` already supports a `department_id` query param.
- `oa/src/store/auth.ts` — `useOaAuth` exposes `user.department_id` (string | null).
- `oa/src/lib/api.ts` — `api.postForm(path, FormData)` posts to the absolute `BASE`; no blob/download helper yet.

## Requirement 1 — Matched DB vendor for Title & Vendor Name

Frontend only (`PaDirectCreatePage.tsx`):

- `Step2Review` `onConfirmed` signature changes to also pass the matched vendor name:
  `onConfirmed(fields, vendorId, vendorName)` where `vendorName = selectedVendor?.name ?? null`.
  (Step 2 already blocks Continue until a DB vendor is selected, so `selectedVendor` is non-null at confirm.)
- Page-level state adds `vendorName` alongside `vendorId`, passed into `Step3PaForm`.
- `Step3PaForm`:
  - New prop `vendorName: string | null` (the matched DB vendor name).
  - Title default: ``Direct Payment — ${vendorName ?? 'Vendor'} ${fields.vendorInvoiceNumber ?? ''}``.trim() — Title stays editable.
  - Vendor Name becomes a **read-only display** of the matched vendor (label + value, no `<input>`). The PA is created with `vendor_name = vendorName`.
  - Remove the editable `vendorName` `useState` input; the submit button's `disabled` no longer depends on a vendor-name text field (it depends on `vendorId` being present, which is guaranteed by Step 2).

No backend change (create already persists the `vendor_name` sent by the client).

## Requirement 2 — Cost Center filtered by user department

Frontend (`Step3PaForm` and the new edit page):

- Read `const { user } = useOaAuth()`.
- Cost-center query becomes:
  ```ts
  const deptId = user?.department_id
  const qs = deptId
    ? `/api/v1/cost-centers?active_only=true&department_id=${deptId}`
    : `/api/v1/cost-centers?active_only=true`
  epmsApi.get<CostCenter[]>(qs)
  ```
  Query key includes `deptId` so it refetches per user.
- Null `department_id` → no `department_id` param → endpoint returns all (fallback).

No backend change (param already supported).

## Requirement 3 — Edit Draft / Returned Direct PA

### Backend (`expense-api`)

1. `schemas/pa.py` — add `cost_center_id: uuid.UUID | None` to `PaResponse`. Add:
   ```python
   class PaDirectUpdate(BaseModel):
       title: str | None = None
       vendor_id: uuid.UUID | None = None
       vendor_name: str | None = None
       budget_account_code: str | None = None
       cost_center_id: uuid.UUID | None = None
       notes: str | None = None
   ```
   All fields optional → partial update; only provided (non-None) fields are applied,
   **except** `notes` and `budget_account_code`/`cost_center_id` which are nullable and
   should support being cleared. To disambiguate "omitted" vs "set to null", use
   `body.model_dump(exclude_unset=True)` and apply only keys present in the request.

2. `crud/pa.py` — add:
   ```python
   async def update_direct_pa(db, pa, changes: dict) -> PaymentApplication:
       for k, v in changes.items():
           setattr(pa, k, v)
       await db.flush(); await db.refresh(pa)
       return pa
   ```

3. `api/v1/pa.py` — add `PATCH /pa/{pa_id}`:
   - Load PA; 404 if missing.
   - 409 if `pa.pa_type != "PA-DIR"` or `pa.status not in ("draft", "returned")`.
   - 403 unless `pa.created_by == user_id` or `role == "system_admin"`.
   - Apply `body.model_dump(exclude_unset=True)` via `update_direct_pa`; return `PaResponse`.
   - Title, if provided, is `.strip()`-ed and must be non-empty (else 422/keep old).

### Frontend (`oa`)

1. New route in `App.tsx`: `<Route path="/pa/:id/edit" element={<PaDirectEditPage />} />`.
2. New `oa/src/pages/pa/PaDirectEditPage.tsx`:
   - Load PA via `GET /api/v1/pa/{id}`; if `status not in (draft,returned)` show a "not editable" message and a link back.
   - Editable fields (Payment Details only):
     - **Title** (text, required).
     - **Matched Vendor** — shows current `vendor_name`; a "Change" control opens the same server-side vendor search used in Step 2 (`GET /api/v1/vendors?...`), storing `vendor_id` + `vendor_name`.
     - **Cost Center** — dept-filtered dropdown (Req 2), prefilled from `pa.cost_center_id`.
     - **Budget Account** — L1/L2 dropdowns from `budgetApi.get('/hierarchy')`; prefill by finding the L1 whose `accounts[].code === pa.budget_account_code`, then select that L2.
     - **Notes** (textarea).
   - Save → `PATCH /api/v1/pa/{id}` with changed fields → navigate back to `/pa/{id}`.
   - Invoice OCR data and amounts are **not** editable.
3. `PaDetailPage.tsx` — in `ActionArea`, the `(status===draft||returned) && isOwner` branch gains an **Edit** button (links to `/pa/{id}/edit`) alongside Submit/Cancel.

## Requirement 4 — Attachments on PA detail page

### Upload (owner, any status)

`PaDetailPage.tsx` `AttachmentsTab`:
- The linked invoice id is `pa.invoice_ids[0]`.
- Add an upload control (click/drag) visible when `perms.is_owner` (or admin). On select:
  `api.postForm('/api/v1/invoice-attachments?invoice_id=<inv>&invoice_source=oa', form)`
  then invalidate `['pa-attachments', pa.id]`. Multiple files allowed (loop).
- Add a **delete** button per attachment for the owner: `api.delete('/api/v1/invoice-attachments/{id}')` then invalidate.
- The upload endpoint already stores the binary on file-api and persists only the `storage_key` — **no DB blob**. Keep using it; do not add any binary-in-DB path.

### Download fix (auth + origin)

The current download is `<a href="/api/v1/invoice-attachments/{id}/file">` — relative
(wrong origin in prod) and carries no bearer token (endpoint requires auth → 401).

- Add `api.getBlob(path): Promise<Blob>` to `api.ts` (fetch absolute `BASE+path` with the
  Authorization header; on !ok throw `ApiError`; on 401 do the same logout redirect as `request`).
- In `AttachmentsTab`, replace the `<a href>` with a Download button that calls
  `api.getBlob('/api/v1/invoice-attachments/{id}/file')`, creates an object URL, triggers a
  download with the attachment's `file_name`, and revokes the URL.

### Create-wizard upload fix (in scope)

`Step3PaForm.handleCreate` currently uploads the invoice file + extra attachments with a raw
relative `fetch` and a hand-rolled token lookup. Replace both with
`api.postForm('/api/v1/invoice-attachments?invoice_id=<inv>&invoice_source=oa', form)` so they
hit the absolute API (Docker-safe) and reuse the shared token handling. Keep uploads non-fatal
(wrap in try/catch) as today.

## Files touched

**Backend (`expense-api`):**
- `app/schemas/pa.py` — add `cost_center_id` to `PaResponse`; add `PaDirectUpdate`.
- `app/crud/pa.py` — add `update_direct_pa`.
- `app/api/v1/pa.py` — add `PATCH /pa/{pa_id}`.

**Frontend (`oa`):**
- `src/lib/api.ts` — add `getBlob`.
- `src/pages/pa/PaDirectCreatePage.tsx` — Req 1, Req 2, create-upload fix.
- `src/pages/pa/PaDetailPage.tsx` — Edit button (Req 3), Attachments upload/delete/download (Req 4).
- `src/pages/pa/PaDirectEditPage.tsx` — new edit page (Req 3).
- `src/App.tsx` — add `/pa/:id/edit` route.

## Verification

- Backend: pytest for the new `PATCH /pa/{id}` — success (draft→updated, returned→updated),
  409 (wrong status / PA-PO), 403 (non-owner), 404 (missing). Run against local docker DB per
  the project's test-DB override.
- Frontend: `tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` in `oa/`.
- Manual:
  - Create Step 3: Title + Vendor Name show the matched DB vendor; vendor name is read-only.
  - Cost Center list limited to the user's department (all if no department).
  - Draft/Returned PA shows Edit; editing fields and saving persists; submitted/approved show no Edit.
  - Attachments tab: owner can upload (file lands on file-api, row appears), download works
    (authenticated), delete works.

## Constraints

- UI strings English-only.
- All attachment binaries on file-api; DB stores only metadata + `storage_key`.
- No git commits this round — changes stay in the working tree for the batch commit.
- New PaResponse field (`cost_center_id`) is additive; no migration (column already exists).
