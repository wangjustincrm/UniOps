# PA Document Chain — Attachment Bundle (Aggregate / Download-all / Print-all)

**Date:** 2026-07-24
**Module:** EPMS frontend (`uniops/epms`)
**Scope:** Frontend-only. No backend changes, no DB migration, no backend image rebuild.

---

## 1. Goal

On the **PA Detail** page, add a button in the **Document Chain** header that opens a
slide-over panel aggregating every attachment along this PA's direct document lineage
(PR, PO, GR, INV, PA). From that panel the user can:

- **Download all** attachments as a single ZIP.
- **Print all** printable attachments in one browser print job. *(Most important requirement.)*
- **Download** a single attachment.
- **Print** a single attachment.

All user-facing strings are English (project convention).

---

## 2. Scope — "Direct lineage"

Walked from the current PA, one line up and down the chain (NOT all siblings on the PO):

| Doc | How resolved |
|-----|--------------|
| **PA** (current) | the page's PA |
| **INV** | `pa.invoice_ids[]` → each invoice |
| **GR**  | union of `invoice.gr_ids[]` across those invoices (de-duplicated) |
| **PO**  | `pa.po_id` |
| **PR**  | `po.pr_id` |

**Excluded:** other invoices / GRs / PAs hanging off the same PO that this PA does not touch.

**Degenerate case:** a prepayment PA with no invoices → lineage collapses to **PA + PO + PR**
(no INV, no GR). Panel still works.

---

## 3. Architecture — frontend aggregation (chosen)

Every attachment is already individually fetchable as an authenticated blob, and lineage
resolution logic already exists (mirrors `DocumentChainTree`). So the whole feature is a
frontend composition — **no backend endpoint, no migration, no epms-api image rebuild.**

### Attachment sources (two services)

| Doc | List endpoint | Single-file endpoint | Meta shape |
|-----|---------------|----------------------|------------|
| PR / PO / GR / PA | epms-api `GET /{type}/{id}/attachments` | `/{type}/{id}/attachments/{attId}/download` | `{id, filename, content_type, file_size, created_at}` |
| INV | expense-api `GET /api/v1/invoice-attachments?invoice_id={id}&invoice_source=epms` | `/api/v1/invoice-attachments/{attId}/file` | `{id, file_name, content_type, file_size_bytes, uploaded_at}` |

The OCR source invoice PDF **is** stored in `invoice-attachments` at invoice creation
(`InvoiceListPage.tsx` upload flow), so the invoice-attachments list is the complete,
retrievable set for an invoice. The `invoices.file_name` column is legacy metadata and is
**ignored** here (would double-count).

### New files

| File | Responsibility |
|------|----------------|
| `epms/src/hooks/useChainAttachments.ts` | Resolve lineage + fetch every doc's attachment list in parallel; return normalized grouped model + loading/error state |
| `epms/src/components/shared/ChainAttachmentsPanel.tsx` | Slide-over UI: grouped list + bulk action bar |
| `epms/src/lib/attachmentBundle.ts` | Pure helpers: authed blob fetch (bounded concurrency), ZIP build, print-document build |

### Changed files

| File | Change |
|------|--------|
| `epms/src/components/shared/DocumentChainTree.tsx` | Turn the `Document Chain` `<h3>` into a flex row; render the trigger button on the right **only when `currentType === 'pa'`**; own the panel open/close state |

### New dependency

- `jszip` (client-side ZIP). `pdfjs-dist` is already a dependency (used by `FilePreviewPanel`).

---

## 4. Data layer — `useChainAttachments(pa)`

1. Resolve lineage doc IDs (reuse `useInvoice`, `useGr`, `usePo`, `usePr`; `invoice_ids` /
   `gr_ids` come off the fetched invoices).
2. For each doc, call its attachment-list endpoint (React Query, parallel).
3. Normalize INV meta (`file_name`→`filename`, `file_size_bytes`→`sizeBytes`) so all groups
   share one shape.
4. Return:

```ts
type ChainAttachment = {
  id: string
  filename: string
  contentType: string
  sizeBytes: number
  fetchUrl: string      // full URL to the single-file endpoint (epms /download vs expense /file)
  printable: boolean    // contentType is application/pdf or image/*
}
type ChainDocGroup = {
  docType: 'PR' | 'PO' | 'GR' | 'INV' | 'PA'
  docNumber: string
  docId: string
  attachments: ChainAttachment[]
}
// ordered: PR, PO, GR(s), INV(s), PA  (paper-trail order)
```

Total count (sum of attachments) drives the button label `Attachments (N)` and disables
bulk actions when 0.

---

## 5. Slide-over panel UI

- Right-side drawer over the PA detail page (full-height overlay; no ancestor-overflow
  clipping concern, so a plain fixed drawer — not a portaled popover — is fine).
- **Bulk action bar (top):** `⬇ Download all (ZIP)` · `🖨 Print all` · close ✕.
  Both bulk buttons disabled when total = 0; show progress text while working
  (`Zipping 5/12…`, `Preparing print 3/8…`).
- **Grouped list:** collapsible group header per doc (`▸ PO-00123 · 3 files`) in paper-trail
  order. Each row: type icon + filename + human size + `⬇` (single download) + `🖨`
  (single print).
- Non-printable rows (docx/xlsx/etc.): `🖨` disabled with tooltip
  *"Preview/print not supported — download instead."*
- Empty state: *"No attachments found in this document chain."*

---

## 6. Single download / single print

- **Single download:** existing pattern — authed fetch → blob → temporary anchor click →
  revoke URL.
- **Single print:** authed fetch → blob →
  - `application/pdf`: render each page with **pdf.js** to a canvas, export as image
    dataURL (same pdfjs pattern as `FilePreviewPanel`).
  - `image/*`: use the blob directly.
  - Write an HTML doc of `<img>` (one per page, each `break-after: page`) into a hidden
    iframe → `iframe.contentWindow.print()`.
  - **Why images, not the PDF itself:** corporate policy blocks native PDF rendering in
    iframes (documented; the reason `FilePreviewPanel` uses pdfjs canvas). We print
    rasterized page images, which is not subject to that block.

---

## 7. Download all (ZIP)

- Bounded-concurrency (limit 4) authed fetch of every attachment blob.
- Add to `JSZip` under `{docType}-{docNumber}/{filename}`; on same-name collision within a
  group, suffix ` (2)`, ` (3)`, …
- `zip.generateAsync({type:'blob'})` → download as `PA-{pa_number}-attachments.zip`.
- A file that fails to fetch is skipped (not fatal); its row is flagged, and a completion
  toast reports *"Skipped N file(s) that failed to download."*
- Progress text on the button during zipping.

---

## 8. Print all (core requirement — detailed)

Produce **one** print job containing every printable attachment across the chain.

1. Enumerate printable attachments (`printable === true`) in paper-trail order.
2. Sequentially (bounded concurrency, but assembled in order): fetch blob →
   - PDF → pdfjs: render every page to canvas → **JPEG** dataURL at a capped scale
     (target ~150 DPI, e.g. `viewport` scale clamped so the longest side ≤ ~2000px) to
     balance crispness vs memory.
   - image → dataURL directly.
   - **Release each canvas** (set width/height to 0, drop refs) immediately after
     extracting its dataURL to keep peak memory bounded even for many-page PDFs.
3. Assemble a single hidden-iframe HTML document: one `<img>` per page, each with
   `break-after: page` and `@page { margin: 12mm }`; a light per-file caption header
   (`PO-00123 · quote.pdf`) is acceptable but optional.
4. Wait for **all** images to finish loading (`img.decode()` / `onload`) before calling
   `iframe.contentWindow.print()` — otherwise blank pages print.
5. Non-printable files are skipped; completion toast: *"Skipped N non-printable file(s)."*
6. Show progress (`Preparing print k/total…`) while rasterizing; disable the button during.

**Memory note:** rasterizing many large PDFs is the heaviest path. Mitigations: sequential
rasterization, JPEG (not PNG) dataURLs, scale cap, eager canvas release. If total printable
page count is very large, this is still acceptable for the expected document volumes (a PA
chain), but the sequential + release strategy is what keeps it from ballooning.

---

## 9. Edge cases & errors

- Prepayment PA (no invoice) → PA+PO+PR only. ✓
- One attachment-list request fails → that group shows an inline error; other groups still
  render; bulk actions operate on what loaded.
- One file fetch fails → skipped in ZIP/print, row flagged, toast reports the count.
- Zero attachments in the whole chain → empty state, bulk buttons disabled.
- Duplicate filenames within a doc → suffixed in ZIP; independent in print.
- Current PA appears in its own `PA` group (expected — it's part of the trail).

---

## 10. Permissions

No new authorization. Every source endpoint already enforces auth (`CurrentUser`). The user
can already navigate to each doc in the chain via the existing Document Chain tree, so
aggregating those attachments grants no access they don't already have. No migration, no
policy change.

---

## 11. Testing

epms frontend has **no unit-test harness** (only `npm run build` = `tsc -b && vite build`,
and `npm run lint`). We do **not** introduce vitest for this feature (YAGNI / stay consistent
with the repo). Verification loop:

- **Type + build:** `npm run build` must pass (catches type errors across new/changed files).
- **Lint:** `npm run lint` clean.
- **Pure helpers written testable:** `attachmentBundle.ts` functions are pure and side-effect
  free where possible, so their logic (collision suffixing, dedup, normalization) is
  reviewable in isolation even without a test runner.
- **Manual QA:** real multi-type chain (PDF + image + docx) →
  single download, single print, download-all ZIP structure, print-all one job +
  page breaks + skipped-count toast, prepayment-PA degenerate chain, empty chain.

---

## 12. Release

- Touches **only the epms frontend** — one service.
- Follow multi-session discipline: one branch, one worktree; commit in logical units; single
  merge point into `main` at release; rebuild only the epms frontend image.
- New dependency `jszip` added to `epms/package.json` (`pdfjs-dist` already present).
- No migration, no backend deploy step.

---

## 13. Out of scope (possible follow-ups)

- Extending the button to PO / PR detail pages (would need PO/PR-centric lineage variants).
- Server-side ZIP endpoint (only worthwhile if we later want a shareable link).
- Rendering Office files (docx/xlsx) for print (currently download-only).
