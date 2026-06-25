# OA module — show only OA-owned PAs & Invoices

**Date:** 2026-06-24
**Status:** Approved, pending implementation
**Scope:** Frontend-only (OA module). Shared backend endpoints unchanged.

## Goal

The OA module's **Payment Applications** and **Invoices** list pages must stop
displaying any EPMS data. Users in OA should only see PAs and Invoices that
belong to the OA module:

- **Payment Applications** → only OA's own Direct PAs (`PA-DIR` from expense-api).
- **Invoices** → only OA invoices (`source = oa`).

This is a frontend-only change. The shared backend endpoints
(`/api/v1/pa`, `/api/v1/invoices/all`) are untouched so the EPMS frontend keeps
working.

## 1. Payment Applications — `oa/src/pages/pa/PaListPage.tsx`

Current behavior: the page issues **two** queries and merges them client-side —
OA's own `PA-DIR` from expense-api (`api.get('/api/v1/pa')`) plus EPMS `PA-PO`
from epms-api (`epmsApi` via `fetchAllPages`).

Changes:

- **Remove the epms-api query** (`pa-list-epms` queryKey, the `fetchAllPages`
  call against `epmsApi`) and the `epmsPas` mapping.
- **Remove the client-side merge/sort.** The list renders OA's `PA-DIR` items
  directly from the expense-api response.
- **Pagination** uses the expense-api response directly (`dirData.items`,
  `dirData.total`) instead of the merged `totalMerged`.
- **Drop the Type column** and the `TypeBadge` component — every row is now
  `PA-DIR`, so the column carries no information. Remaining table columns:
  PA Number, Vendor, Amount, Status, Date.
- **Remove the EPMS deep-link banner** (the `source=epms` + `po_id` /
  `po_number` block). It is EPMS-context display UI that no longer belongs on an
  OA-only list. It is display-only and does not drive PA creation, so removal is
  safe.
- **Remove now-unused code:** `epmsApi` and `fetchAllPages` imports (if unused
  elsewhere in the file), the `EpmsPaItem` / `EpmsPaList` interfaces, and the
  `po_number` field on the `Pa` interface if it becomes unused.

The "Direct PA" create button and the status filter tabs are unchanged.

## 2. Invoices — `oa/src/pages/invoices/InvoicesPage.tsx`

Current behavior: queries `/api/v1/invoices/all` (which returns both EPMS and OA
invoices), with All / EPMS / OA source tabs and a 3-card stats grid.

Changes:

- **Always request `source=oa`** from `/api/v1/invoices/all`. The backend
  already supports this query param and returns only OA `expense_invoices`.
- **Remove the source filter tabs** (All / EPMS / OA) and the `sourceFilter`
  state — only OA remains.
- **Stats row:** replace the 3-card All/EPMS/OA grid with a single "Total" card
  showing `data.total`.
- **Drop the Source column** and the `SOURCE_STYLE` badge — every row is `oa`.
- **Header subtitle** updated from "All vendor invoices from EPMS and OA in one
  place" to OA-only wording (e.g. "Vendor invoices for direct payments").
- Detail navigation still uses `/invoices/:source/:id`; `source` is always `oa`.
- Remove now-unused code: `SourceFilter` type, `SOURCE_STYLE`, EPMS-only status
  entries / `internal_ref` rendering can be left if harmless but the EPMS
  `internal_ref` sub-label block can be removed since `source` is always `oa`.

## Out of scope

- Backend `/api/v1/invoices/all` and `/api/v1/pa` endpoints (shared with EPMS) —
  unchanged.
- Detail pages (`PaDetailPage`, `InvoiceDetailPage`). They are reachable only
  from the lists; an EPMS id can no longer appear there, so no change is needed.
- PA create flows (`/pa/new`, `/pa/new/direct`).

## Verification

- TypeScript typecheck:
  `tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` in `oa/`.
- Manual:
  - OA Payment Applications shows only Direct PAs; no PA-PO rows, no Type
    column, no EPMS banner.
  - OA Invoices shows only OA invoices; no source tabs, no Source column, no
    EPMS rows.

## Notes

- UI strings stay English-only (per project convention).
- Not committed to git this round — changes stay in the working tree until the
  batch commit.
