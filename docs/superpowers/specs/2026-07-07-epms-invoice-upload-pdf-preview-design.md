# EPMS Upload Invoice — PDF Preview Panel (Design)

**Date:** 2026-07-07
**Status:** Approved by user (layout mockup confirmed)
**Scope:** Frontend only — `uniops/epms/src/pages/invoices/InvoiceListPage.tsx` (`UploadModal`)

## Goal

When a user uploads an invoice file in the EPMS Upload Invoice modal, show a live
preview of the uploaded PDF/image to the **left** of the form so the user can
manually verify the AI-parsed field values against the source document.

## Approved Layout (per user mockup)

- **No file selected:** modal stays as today — narrow `max-w-xl`, single column,
  drop zone + form stacked.
- **File selected:** modal widens to `max-w-6xl`. Structure:
  - **Top (full width):** the existing drop zone, collapsed to the file chip row
    (file name, size, parsing spinner, remove ×) — unchanged behavior.
  - **Below, two columns:**
    - **Left (~55%):** preview panel. Dark-neutral background, independently
      scrollable, fills available height (modal keeps `max-h-[92vh]`).
    - **Right (~45%):** the entire existing form (vendor, invoice #, PO number,
      dates, amounts, line items, notes) — unchanged fields and logic,
      independently scrollable.
  - **Footer:** Cancel / Upload Invoice buttons, unchanged.
- **Removing the file** (× on the chip) collapses the modal back to the narrow
  single-column layout.

## Preview Rendering — native browser (zero dependencies)

- On file select, create `URL.createObjectURL(file)`.
- **PDF:** render in an `<iframe>` — the browser's built-in PDF viewer provides
  paging, zoom, and text search natively. The "Page < 1/4 >" control in the
  mockup is therefore covered by the native viewer toolbar; no custom pager.
- **JPG/PNG:** render in an `<img>` with `object-contain` inside a scrollable
  container.
- **Cleanup:** `URL.revokeObjectURL()` when the file is removed/replaced or the
  modal unmounts (avoid blob memory leaks).
- **Panel header:** small toolbar with the file name and an "Open in new tab"
  link (opens the blob URL in a full browser tab).
- Rejected alternative: `react-pdf`/pdf.js custom rendering (~400 KB bundle) —
  only needed if we later want to highlight parsed-field locations on the page.
  YAGNI for now.

## Component Boundaries

- Extract a small presentational component `FilePreviewPanel`
  (props: `file: File`) that owns blob-URL lifecycle and PDF-vs-image switching.
  Lives in `InvoiceListPage.tsx` alongside `UploadModal` (or `components/` if it
  grows). No changes to parsing (`parseInvoiceFile`), submit, attachment upload,
  or PO-matching logic. No backend changes.

## Error Handling

- Unsupported file type (input `accept` should already prevent this): left panel
  shows a "Preview not available" placeholder; the form remains fully usable.
- Blob URL creation failure must never block form entry.

## UI Text

All user-facing strings in English (per project convention).

## Testing / Verification

- Existing vitest suite passes.
- Manual verification: PDF preview renders with native toolbar; JPG/PNG preview
  renders; replacing the file swaps the preview; removing the file collapses the
  modal back to narrow; no blob-URL leaks (revoke on remove/unmount);
  responsive sanity check at ~1280px width.
