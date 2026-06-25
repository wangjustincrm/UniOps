# PRD-OA — OA Module Changes & Improvements

**Date:** 2026-05-05
**Module:** `uniops/oa` + `uniops/expense-api`
**Feature scope:** Direct Payment Application (PA-DIR) creation flow

> **⚠️ Historical document (superseded 2026-06-02).** This file records the original PA-DIR change set. Two paths below have since moved:
> - Budget endpoints (`/budget/accounts`, `/budget/hierarchy`) referenced as `expense-api/app/api/v1/budget.py` now live in the standalone **budget-api (:8007)**; the OA frontend calls budget-api directly.
> - Invoice OCR moved from the browser (`oa/src/lib/invoice-parser.ts` calling Claude directly) to a server-side endpoint **`POST /api/v1/ocr/{mode}`** in expense-api; the Anthropic API key is no longer in the browser bundle.
>
> The authoritative spec is `epms/docs/PRD-OA.md` v2.0. See `SPRINT-OA-FIX.md` for the remediation that introduced these changes.

---

## Overview

This document captures all changes made to the OA module's **Direct Payment Application** flow, covering frontend (`oa/`), backend (`expense-api/`), and database schema.

---

## 1. Invoice PDF Preview

### Problem
After uploading an invoice in Step 1, the PDF preview area in Step 2 showed a Chrome PDF viewer placeholder (UUID + Open button) instead of the actual PDF content. Chrome's built-in PDF viewer does not render blob: URLs reliably in `<iframe>` / `<embed>` / `<object>` tags, especially when the user has "Download PDFs" enabled.

### Solution
Replaced native browser PDF rendering with **PDF.js canvas rendering** (`pdfjs-dist` v5.x, already in dependencies).

**File:** `oa/src/pages/pa/PaDirectCreatePage.tsx`

- Added `PdfPreview` component: renders each PDF page to a `<canvas>` using `pdfjsLib.getDocument()` + `page.render()`
- Supports multi-page PDFs with prev/next pagination
- Renders at scale 2.0 for sharp display
- Added `ImagePreview` component for non-PDF files (JPG, PNG, WebP) using `URL.createObjectURL` within `useEffect` to avoid React StrictMode double-mount revocation
- PDF.js worker configured via `import.meta.url` (Vite-compatible):
  ```ts
  pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
    'pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url
  ).href
  ```

### Preview Area Sizing
- Width: `720px` fixed (left column)
- Height: `min-h-[800px]`
- Layout: flex row — preview (720px) + fields (400px) in `max-w-[1200px]` container

---

## 2. Step 2 — Review Extracted Data

### 2.1 Line Items Parsing & Editing

**Problem:** AI already extracts `lineItems` from the invoice but Step 2 had no UI to display or edit them.

**Changes (`PaDirectCreatePage.tsx`):**

- Added `lineItems` state initialized from `initial.lineItems`
- Displayed extracted line items in an editable table (Description, Qty, Unit Price, Total)
- Qty × Unit Price auto-recalculates Total on change
- Add / remove line item buttons
- Subtotal row at the bottom
- `handleConfirm` passes `lineItems` through to Step 3 and ultimately the API

### 2.2 Total Field

**Problem:** Step 2 showed Subtotal and Tax Amount separately but no Total.

**Changes:** Added a read-only **Total (Subtotal + Tax)** field below Tax Amount, styled in primary teal to distinguish from editable fields. Recalculates live as user edits Subtotal or Tax Amount.

### 2.3 Vendor Matching (DB)

**Problem:** AI extracts a vendor name string but the PA requires a real `vendor_id` from the database. No matching was performed.

**Solution:** Server-side vendor search matching EPMS PR Create pattern.

**Frontend changes (`PaDirectCreatePage.tsx`):**
- `vendorQuery` state seeded with AI-extracted vendor name on mount
- `useQuery(['vendors-search', vendorQuery])` re-fetches on every keystroke with `search=` parameter (server-side filtering, 50 results per page)
- Auto-selects first exact or partial match from server results
- If no match: red warning block, searchable dropdown for manual selection
- If matched: green indicator + "Change" link
- `selectedVendorId` passed to `onConfirmed` callback → Step 3 → API

**Backend changes (`expense-api/app/api/v1/vendors.py`):** New file
```
GET /api/v1/vendors?search=&active_only=true&page_size=50
```
- Reads from shared DB `vendors` table via `EpmsVendor` mirror model
- Server-side ILIKE search on `name` and `code`
- Returns `{ items: VendorOut[], total: int }`

**Model changes (`expense-api/app/models/epms_mirrors.py`):**
- Added `EpmsVendor.is_active` field
- Added `EpmsCostCenter` mirror model (`cost_centers` table)
- Added `EpmsBudgetL1` mirror model (`budget_l1` table)

**Registered in `expense-api/app/main.py`:**
```python
app.include_router(vendors_router, prefix="/api/v1")
```

### 2.4 Field Ordering

Fields in right column (top to bottom):
1. Vendor Name *(AI extracted)*
2. **Matched Vendor (DB)** ← inserted here
3. Invoice Number
4. Invoice Date
5. Due Date
6. Currency
7. Subtotal
8. Tax Amount
9. **Total** *(calculated, read-only)*
10. **Line Items** *(editable table)*

---

## 3. Step 3 — Payment Details

### 3.1 Title Field

**Change:** Added **Title** text input above Vendor Name. Default value auto-generated as:
```
Direct Payment — {VendorName} {InvoiceNumber}
```
User can edit before submitting. Passed as `title` to `POST /api/v1/pa/direct`.

### 3.2 Budget Account (3-Level Cascade)

**Problem:** No budget account selection in Direct PA flow.

**Solution:** 3-level cascading selector matching EPMS Create PR.

**Frontend (`PaDirectCreatePage.tsx`):**
- **Level 1 — Cost Center** dropdown (all active cost centers)
- **Level 2 — L1 Category** dropdown (filtered by selected cost center, enabled after CC selected)
- **Level 3 — L2 Account** dropdown (filtered by selected L1, enabled after L1 selected)
- Selecting a higher level clears lower-level selections
- Selected L2 account code shown as confirmation
- `budget_account_code` (L2 code) passed to API

**Backend (`expense-api/app/api/v1/budget.py`):** New endpoint
```
GET /api/v1/budget/hierarchy
```
Returns full Cost Center → L1 → L2 hierarchy:
```json
[
  {
    "id": "...", "code": "GA-0100", "name": "...",
    "l1_groups": [
      {
        "id": "...", "code": "CRM001", "name": "TRAVEL",
        "accounts": [{ "id": "...", "code": "CRM00101", "name": "Travel Meals" }]
      }
    ]
  }
]
```

**Database:**
```sql
ALTER TABLE payment_applications ADD COLUMN budget_account_code VARCHAR(100) NULL;
```

**Schema (`expense-api/app/schemas/pa.py`):**
```python
budget_account_code: str | None = None
```

**Model (`expense-api/app/models/pa.py`):**
```python
budget_account_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
```

### 3.3 Additional Attachments Upload

**Change:** Added **Additional Attachments** block between Amount breakdown and Notes.

- Multi-file selector (any file type)
- Files listed with name, size, and remove button
- On PA creation, each file is uploaded to `/api/v1/invoice-attachments?invoice_id=&invoice_source=oa` (same endpoint as the invoice file)
- Non-fatal: attachment upload failures do not block PA creation

### 3.4 Line Items Display (Read-Only)

**Change:** Line items from Step 2 are displayed as a read-only table in Payment Details between Vendor Name and Amount breakdown, giving Finance the full picture before approving.

---

## 4. Database Schema Fixes

Several `payment_applications` columns had NOT NULL constraints that were incompatible with Direct PA (which has no PO):

```sql
ALTER TABLE payment_applications ALTER COLUMN po_id       DROP NOT NULL;
ALTER TABLE payment_applications ALTER COLUMN po_number   DROP NOT NULL;
ALTER TABLE payment_applications ALTER COLUMN vendor_id   DROP NOT NULL;
```

---

## 5. Cross-Service FK Fix (expense-api)

**Problem:** `payment_applications` model had `ForeignKey("users.id")`, `ForeignKey("vendors.id")`, `ForeignKey("purchase_orders.id")` — tables from epms-api not registered in expense-api's SQLAlchemy metadata. This caused `NoReferencedTableError` on startup.

**Fix (`expense-api/app/models/pa.py`):** Removed `ForeignKey()` decorators from cross-service columns; columns remain as plain `UUID(as_uuid=True)`. Database-level FK constraints are still enforced by PostgreSQL.

---

## 6. Authentication & Session Handling

### Auth Guard (OA AppLayout)
**File:** `oa/src/components/layout/AppLayout.tsx`

Added token check on mount. If no token found in `oa-auth` or `portal-auth` localStorage, redirect to portal:
```
http://localhost:5174?returnUrl=<current OA URL>
```
The portal will complete login and perform session handoff via `#__session=<base64>` hash fragment.

### 401 Auto-Redirect (`oa/src/lib/api.ts`)
Any 401 response from expense-api triggers redirect to portal for re-authentication.

### Access Token Lifetime (`docker-compose.dev.yml`)
`epms-api` `ACCESS_TOKEN_EXPIRE_MINUTES` increased from `15` → `480` minutes to avoid frequent session expiry during development.

---

## 7. File Changes Summary

| File | Type | Description |
|------|------|-------------|
| `oa/src/pages/pa/PaDirectCreatePage.tsx` | Modified | All Step 2 & 3 UI changes |
| `oa/src/lib/api.ts` | Modified | 401 redirect + `EPMS_BASE` constant |
| `oa/src/components/layout/AppLayout.tsx` | Modified | Auth guard |
| `expense-api/app/api/v1/vendors.py` | **New** | Vendor search endpoint |
| `expense-api/app/api/v1/budget.py` | Modified | Added `/budget/hierarchy` endpoint |
| `expense-api/app/models/pa.py` | Modified | Removed cross-service FKs, added `budget_account_code` |
| `expense-api/app/models/epms_mirrors.py` | Modified | Added `EpmsCostCenter`, `EpmsBudgetL1`, `EpmsVendor.is_active` |
| `expense-api/app/schemas/pa.py` | Modified | Added `budget_account_code`, `title` to `PaDirectCreate` |
| `expense-api/app/api/v1/pa.py` | Modified | Stores `budget_account_code` on PA creation |
| `expense-api/app/main.py` | Modified | Registered `vendors_router` |
| `docker-compose.dev.yml` | Modified | `ACCESS_TOKEN_EXPIRE_MINUTES: "480"` |

---

## 8. API Reference

### POST /api/v1/pa/direct
```json
{
  "invoice_id": "uuid",
  "vendor_id": "uuid | null",
  "vendor_name": "string",
  "title": "string | null",
  "payment_amount": 213.02,
  "currency": "CAD",
  "notes": "string | null",
  "budget_account_code": "CRM00101 | null"
}
```

### GET /api/v1/vendors
```
?search=titan&active_only=true&page_size=50
```
```json
{ "items": [{ "id": "uuid", "name": "Titan Power Ltd", "code": "V-001" }], "total": 1 }
```

### GET /api/v1/budget/hierarchy
```json
[{ "id": "uuid", "code": "GA-0100", "name": "General Admin", "l1_groups": [...] }]
```
