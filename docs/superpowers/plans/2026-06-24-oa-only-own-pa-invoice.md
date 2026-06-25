# OA-Only PAs & Invoices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the OA module's Payment Applications and Invoices list pages show only OA-owned data (Direct PAs and OA invoices), with no EPMS data.

**Architecture:** Frontend-only changes in two React pages. PA list drops its second (epms-api) query and client-side merge. Invoices list pins the existing backend `source=oa` query param. Shared backend endpoints are untouched.

**Tech Stack:** React + TypeScript (TS 6.0.3), @tanstack/react-query, Vite, Tailwind.

## Global Constraints

- No git commits / pushes / branches this round — all changes stay in the working tree (project standing instruction).
- All user-facing strings English-only.
- Typecheck command (run in `uniops/oa/`): `tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` (TS 6.0.3 `tsc -b` is broken on deprecated `baseUrl`).
- Backend endpoints `/api/v1/pa` and `/api/v1/invoices/all` are shared with EPMS — do NOT modify them.

---

### Task 1: Payment Applications — show only OA Direct PAs

**Files:**
- Modify: `uniops/oa/src/pages/pa/PaListPage.tsx`

**Interfaces:**
- Consumes: `api.get<PaList>('/api/v1/pa?...')` from `@/lib/api` (unchanged) returning `{ items: Pa[]; total: number }`.
- Produces: nothing for later tasks (page-local change).

- [ ] **Step 1: Remove the EPMS query, merge, and EPMS types**

In `PaListPage.tsx`:
- Delete the `epmsData` `useQuery` block (queryKey `['pa-list-epms', status]` using `fetchAllPages`/`epmsApi`).
- Delete the `epmsPas` mapping array.
- Delete the `EpmsPaItem` and `EpmsPaList` interfaces.
- Replace the merged-list logic with direct use of the expense-api response:

```tsx
const items: Pa[] = dirData?.items ?? []
const total = dirData?.total ?? 0
const isLoading = dirLoading
const error = dirError
```

Remove the now-dead `mergedItems` / `totalMerged` / `epmsLoading` / `epmsError` references and rename their usages below to `items` / `total`.

- [ ] **Step 2: Drop the Type column and EPMS deep-link banner**

- Remove the `<TypeBadge .../>` `<td>` cell and the "Type" `<th>` header cell from the table.
- Delete the `TypeBadge` function component (now unused).
- Delete the entire `{fromEpms && poId && ( ... )}` EPMS deep-link banner block, plus the `poId` / `poNumber` / `fromEpms` variables that feed only it.

The table header becomes: PA Number, Vendor, Amount, Status, Date. Each row's cells must match (remove the Type `<td>`).

- [ ] **Step 3: Clean up imports and the Pa interface**

- Remove `epmsApi` and `fetchAllPages` from the `@/lib/api` import (keep `api`). Verify they are not used elsewhere in the file first.
- In the `Pa` interface, remove `pa_type` and `po_number` if they are no longer referenced anywhere in the file after the edits. Keep `created_at` only if still used by the sort/date logic; since the merge sort is gone, the date cell uses `pa.submitted_at` via `formatDate` — confirm and remove unused fields.
- If `useSearchParams` is still used for the status filter tabs, keep it; otherwise remove. (The status tabs use `setParams`, so it stays.)

- [ ] **Step 4: Typecheck**

Run (in `uniops/oa/`): `tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: PASS with no errors (in particular, no "unused variable" or "cannot find name" errors for removed `TypeBadge`, `epmsApi`, `fetchAllPages`, `mergedItems`, `totalMerged`).

- [ ] **Step 5: Manual verification**

Load the OA Payment Applications page. Expected: only Direct PAs listed; no Type column; no "Creating payment … from EPMS" banner; pagination reflects the expense-api total; status filter tabs still work.

---

### Task 2: Invoices — show only OA invoices

**Files:**
- Modify: `uniops/oa/src/pages/invoices/InvoicesPage.tsx`

**Interfaces:**
- Consumes: `api.get<InvoiceList>('/api/v1/invoices/all?...&source=oa')` from `@/lib/api`. The backend already filters to OA `expense_invoices` when `source=oa`.
- Produces: nothing for later tasks (page-local change).

- [ ] **Step 1: Pin source=oa in the query**

In the `useQuery` `queryFn`, always set `source=oa` and drop the `sourceFilter` dependency:

```tsx
const { data, isLoading, error } = useQuery<InvoiceList>({
  queryKey: ['oa-invoices', search, page, pageSize],
  queryFn: () => {
    const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize), source: 'oa' })
    if (search) qs.set('search', search)
    return api.get<InvoiceList>(`/api/v1/invoices/all?${qs}`)
  },
  staleTime: 30_000,
})
```

- [ ] **Step 2: Remove source tabs, source state, and SOURCE_TABS**

- Delete the `sourceFilter` state (`const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all')`).
- Delete the `SourceFilter` type and the `SOURCE_TABS` array.
- Delete the JSX block rendering the source filter tabs (the `<div className="flex gap-1 border-b ...">` that maps over `SOURCE_TABS`).

- [ ] **Step 3: Replace stats grid with a single Total card**

Replace the 3-card grid (`grid-cols-3` mapping over Total/EPMS/OA) with a single Total card:

```tsx
<div className="rounded-xl border border-neutral-200 bg-white p-4 text-center max-w-[200px]">
  <p className="text-xs text-neutral-400">Total</p>
  <p className="text-2xl font-bold text-neutral-900 mt-1">{data?.total ?? 0}</p>
</div>
```

- [ ] **Step 4: Drop the Source column and EPMS-only rendering**

- Remove `'Source'` from the table header array and remove the corresponding source-badge `<td>` cell from each row.
- Delete the `SOURCE_STYLE` constant (now unused).
- Remove the EPMS `internal_ref` sub-label block (`{inv.source === 'epms' && inv.internal_ref && (...)}`) since `source` is always `oa`.
- Update the header subtitle from "All vendor invoices from EPMS and OA in one place" to "Vendor invoices for direct payments".
- The Reference column keeps showing `inv.po_number || inv.pa_number || '—'` (for OA rows this is `pa_number`).

- [ ] **Step 5: Typecheck**

Run (in `uniops/oa/`): `tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: PASS with no errors (no "unused" errors for `SourceFilter`, `SOURCE_TABS`, `SOURCE_STYLE`, `sourceFilter`).

- [ ] **Step 6: Manual verification**

Load the OA Invoices page. Expected: only OA invoices listed; no source filter tabs; no Source column; single Total card; navigation to detail (`/invoices/oa/:id`) still works.

---

## Notes

- No commit steps: per the project standing instruction, leave all changes in the working tree for a later batch commit.
- Detail pages (`PaDetailPage`, `InvoiceDetailPage`) need no change — EPMS ids can no longer reach them from these lists.
