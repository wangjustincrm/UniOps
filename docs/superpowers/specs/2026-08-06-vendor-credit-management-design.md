# Vendor Credit Management — Design

- **Date**: 2026-08-06
- **Status**: Approved design, not yet planned or implemented
- **Owning service**: `finance-api` (all tables, all APIs)
- **Frontends touched**: `epms` (upload + review), `finance` (QBO import admin)

---

## 1. Problem

AP receives vendor Credit Notes in the same email stream as regular invoices. They
are negative-value documents (see the Amazon Business credit note, total
`-CA$0.04`, referencing `PO-089-2605-23`). EPMS **Upload Invoice** rejects them at
four separate gates, so today they cannot be recorded at all:

| Layer | Location | Current constraint |
|---|---|---|
| Frontend submit gate | `epms/src/pages/invoices/InvoiceListPage.tsx:264` | `amtNum <= 0` → silent `return` |
| Frontend input | `epms/src/pages/invoices/InvoiceListPage.tsx:675` | `<input min={0}>` |
| Backend header schema | `epms-api/app/schemas/invoice.py:27` | `amount: Decimal = Field(gt=0)` (same on `InvoiceUpdate:42`) |
| Downstream PA | `epms-api/app/schemas/pa.py:57` | `subtotal ge=0`, `qty gt=0` |

Line-level negatives were already relaxed for discount lines (see
`project_uniops_gr_negative_discount_line`); only **header** amounts and the
downstream PA remain constrained.

The requirement is **not** "allow negative invoices". It is a **Vendor Credit
subsystem**: detect credit notes at upload, hold them as a per-vendor credit
balance, and automatically net them off the next payment to that vendor.

---

## 2. Scope

**In scope**

1. Detect a credit note at upload (OCR signal + negative-total fallback) and ask
   the uploader to confirm the document type.
2. Store confirmed credit notes as `vendor_credits` rows, pending AP review.
3. AP reviews (approve → available, reject → void) on a dedicated page.
4. At PA payment execution, automatically apply available credits FIFO, with AP
   able to deselect individual credits before executing.
5. Import existing QBO Vendor Credits as opening balances, via a vendor-mapping
   workbench.

**Out of scope (explicitly deferred)**

- Posting credit notes as negative AP invoices / AP aging integration.
- Syncing credits back out to NC or QBO.
- FX conversion between credit currency and payment currency.
- Segregation of duties on credit review (self-review is allowed — see §6.3).

---

## 3. Architecture decision: finance-api owns everything

Three options were considered.

| Option | Verdict |
|---|---|
| **1. finance-api owns tables + all APIs** (chosen) | Credit consumption happens inside `payment_execute.execute()`; owning the tables there keeps consumption atomic with the payment record in one transaction. Same domain as `payment_records` / `ap_invoices` / `payment_batches`. Single alembic chain. |
| 2. epms-api owns tables, finance-api calls back over HTTP | Rejected: payment execution and credit decrement would be non-atomic across services. A failed HTTP call leaves money paid but credit unconsumed (or vice versa). |
| 3. Tables in finance-api, **write** mirror models in epms-api | Rejected: `feedback_uniops_mirror_models_match_reality` records three prior incidents with read mirrors; a write mirror is worse, and two alembic chains co-owning one table has no clear owner. |

The EPMS frontend already has a finance-api client (`epms/src/lib/api.ts:192`,
`FINANCE_BASE` + `/finance/v1`), so calling finance-api from EPMS pages is an
established pattern, not a new one.

---

## 4. Data model

Three new tables, all in `finance-api`, one alembic migration.

### 4.1 `vendor_credits`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `credit_number` | String(30) unique | `VC-YYYYMMDD-NNNN`, allocated via `finance-api/app/crud/_numbering.py` (advisory-lock allocator, see `project_uniops_document_number_collision`) |
| `vendor_id` | UUID | → `business_partners.id` |
| `vendor_name` | String(255) | snapshot |
| `vendor_credit_number` | String(100) | the vendor's own number (e.g. `11DJ-MFHX-N4JG`) |
| `credit_date` | Date | |
| `currency` | String(10) | |
| `amount` / `tax_amount` / `total_amount` | Numeric(15,2) | **always positive** |
| `applied_amount` | Numeric(15,2) default 0 | positive |
| `remaining_amount` | Numeric(15,2) | stored column, `= total_amount - applied_amount` |
| `status` | String(20) | `pending_review` \| `available` \| `exhausted` \| `void` |
| `po_id` / `po_number` | nullable | **traceability only** — never 3-way matched |
| `line_items` | JSONB | archival copy |
| `file_name` | String(255) | binary lives in the shared attachment store (§6.4) |
| `notes` | Text | |
| `source` | String(20) NOT NULL default `upload` | `upload` \| `qbo_import` |
| `source_ref` | String(20) nullable | QBO `qbo_id` for imported rows |
| `opening_balance` | Boolean default false | true for QBO-imported rows |
| `imported_from_sync_run_id` | UUID nullable | → `qbo_sync_runs.id` of the FULL RELOAD used as cutover |
| `uploaded_by` / `uploaded_at` | | |
| `reviewed_by` / `reviewed_at` / `review_note` | nullable | |

**Constraints**

- `CHECK (total_amount > 0)`
- `CHECK (applied_amount >= 0 AND remaining_amount >= 0)`
- `CHECK (applied_amount + remaining_amount = total_amount)`
- Partial unique index on `(vendor_id, vendor_credit_number) WHERE status <> 'void'` — blocks duplicate credit notes at insert time while letting a rejected one be re-uploaded. **Deliberately NOT scoped to `source = 'upload'`**: the same vendor document arriving both by manual upload and by a Phase C QBO import would otherwise create two rows and double the credit pool, invisibly to the §7.5 drift detection since both rows look legitimate.
- Partial unique index on `(source, source_ref) WHERE source_ref IS NOT NULL` — makes the QBO import idempotent
- Partial index on `(vendor_id, currency) WHERE status = 'available' AND remaining_amount > 0` — serves the FIFO lookup

### 4.2 `vendor_credit_applications`

One row per (credit, payment) application.

`id` · `credit_id` FK → `vendor_credits` · `payment_record_id` · `batch_id` ·
`doc_kind` / `doc_id` / `doc_number` (the PA being paid) · `applied_amount`
(positive) · `applied_at` · `applied_by`

### 4.3 `qbo_vendor_map`

Persists AP's mapping decisions so re-running the import never re-asks.

| Column | Notes |
|---|---|
| `qbo_vendor_id` | String(20) PK |
| `qbo_display_name` | snapshot — survives renames in QBO |
| `vendor_id` | UUID nullable → `business_partners.id` |
| `decision` | `pending` \| `mapped` \| `ignored_employee` \| `ignored_other` |
| `decided_by` / `decided_at` / `note` | |

### 4.4 `payment_records` — one new column

`credit_applied` Numeric(15,2) NOT NULL default 0. `amount` continues to hold the
**net cash** actually paid.

### 4.5 Sign convention (decided)

**Every monetary column in `vendor_credits` is positive.** A vendor-issued credit
note showing `-0.04` is stored as `0.04`. "This is a credit" is expressed by the
table it lives in, not by a sign.

`abs()` is applied in **exactly one place** — the finance-api write layer
(create + import) — backed by `CHECK (total_amount > 0)`. The frontend *displays*
positive values and *submits* positive values, but is not the source of truth for
normalisation.

This matters because `expense-api/app/services/ocr_service.py`
`_normalize_negative_quantities()` already flips sign once (negative qty →
positive qty with negated unit price, see
`project_uniops_ocr_negative_qty_normalize`). A second flip in the frontend would
desync `qty × unit_price` from the line amount. **The existing OCR normalisation
is not modified by this design.**

### 4.6 State machine

```
manual upload ──> pending_review ──approve──> available ──apply──> exhausted
                        │                         │      (remaining → 0)
                      reject                     void
                        ↓                (only while applied_amount = 0)
                      void
QBO import ─────────────────────────────> available
```

QBO-imported credits skip review: the data was already reconciled by accounting
inside QBO. The human judgement that *does* matter for imported rows is the
vendor mapping, which is gated separately (§7).

---

## 5. Detection, confirmation, review

### 5.1 Detection — two independent signals, OR'd

1. **OCR**: `expense-api` `POST /api/v1/ocr/invoice` gains a
   `document_type: "invoice" | "credit_note"` field. The prompt keys on
   Credit Note / Credit Memo / Credit Invoice / Adjustment Note / Avoir /
   贷项通知单.
2. **Structural fallback (frontend)**: document total or pre-tax subtotal is
   negative.

### 5.2 Confirmation UX

The document-type selector is **always present**, not only when detection fires:

- Regular invoice → collapsed one-liner: `Document type: Invoice · Change`
- Detected credit → expanded banner:
  `⚠ This looks like a Credit Note (total is negative).  ( ) Regular Invoice  (•) Credit Note`

Always-present because the reverse miss is also real: a credit note that neither
says "Credit" nor carries a negative total will never be auto-detected, and the
cost of missing it is money. The control is cheap; the miss is not.

When **Credit Note** is selected, the upload form changes:

| Field | Change |
|---|---|
| Title / submit button | `Upload Credit Note` |
| `Vendor Invoice #` | → `Credit Note #` |
| `Amount (pre-tax)` | → `Credit Amount (pre-tax)`, absolute value, positive |
| `Tax Amount` | → `Credit Tax`, positive |
| `Due Date` | hidden — a credit has no due date |
| `PO Number` | kept, labelled `optional — reference only, not 3-way matched`; the auto-match block at `InvoiceListPage.tsx:315` is skipped |
| Line items | kept for the archive, displayed positive |
| Submit target | `POST /finance/v1/vendor-credits` |

Duplicate detection: the server checks `(vendor_id, vendor_credit_number)` and
returns **409** with a link to the existing credit, mirroring the existing
`isDuplicate` experience for invoices.

### 5.3 Review page — EPMS `/vendor-credits`

Placed in EPMS (not the Finance app) because the main invoice entry point lives
there and AP should not have to switch apps mid-task.

- Tabs: `Pending Review` / `Available` / `Exhausted` / `Void`, plus an
  `Opening balance` filter for QBO-imported rows
- Inline original-document preview using the pdfjs + canvas `PdfPreview`
  component — **never an iframe**; native PDF embedding is blocked by company
  policy (`feedback_uniops_pdf_preview_use_pdfjs`)
- `Approve` → `available`; `Reject` → `void` with a mandatory note
- Available rows show `Original / Applied / Remaining` and expand to the
  application history

---

## 6. Payment application

### 6.1 Where

`finance-api/app/crud/payment_execute.py` `execute()`, PA branch: after `amount`
is resolved (line 309) and before the `PaymentRecord` is constructed. Both
single-PA payment and batch execution route through this function, so one change
covers both.

**Which documents are eligible.** `execute()` branches on
`req.doc_kind in ("pa", "pa_dir")` (line 258) — both resolve to the same
`payment_applications` table, `pa_dir` being the PO-less Direct PA that OA
creates. Both are payments to a vendor, so **both are eligible for credit
application**. The separate `expense_claim` branch is **not** eligible: an expense
claim pays an employee, and a vendor credit has no business reducing it. This is
enforced by the `vendor_id` + `currency` predicate — an expense claim has no
`vendor_id` — but the eligibility check is written explicitly rather than left to
fall out of a join.

### 6.2 Preview endpoint

`GET /finance/v1/vendor-credits/suggest?doc_kind=pa&doc_id=…` returns:

```json
{
  "gross": "12500.00",
  "suggested": [
    {"credit_id": "…", "credit_number": "VC-20260611-0007",
     "credit_date": "2026-06-11", "remaining": "0.04", "apply": "0.04"}
  ],
  "credit_applied": "0.04",
  "net": "12499.96"
}
```

Both the single-payment confirmation dialog and the batch execution confirmation
page call this first and let AP deselect individual credits.

### 6.3 Execution

`PaymentExecuteRequest` gains `credit_ids: list[UUID] | None`:

- `None` → apply the FIFO default (this is the "automatic" behaviour)
- `[]` → explicitly apply nothing this run
- `[ids]` → apply only these

Selection query:

```sql
SELECT ... FROM vendor_credits
 WHERE vendor_id = :vendor AND currency = :currency
   AND status = 'available' AND remaining_amount > 0
 ORDER BY credit_date, created_at        -- FIFO
```

**Locking differs by mode, deliberately:**

- Default FIFO → `FOR UPDATE SKIP LOCKED`. Two batches paying the same vendor
  concurrently take disjoint credits: no double-spend, no blocking.
- Explicit `credit_ids` → plain `FOR UPDATE`, and if any selected credit is no
  longer `available`, **the whole execution fails and rolls back**. Silently
  applying less than the preview showed would break AP's trust in the preview.

Arithmetic:

```
base            = req.amount_paid ?? pa.payment_amount
applied_i       = min(credit_i.remaining_amount, base - Σ applied_so_far)
credit_applied  = Σ applied_i
net             = base - credit_applied          # floors at 0, never negative
```

`PaymentRecord.amount = net`, `PaymentRecord.credit_applied = credit_applied`.
Each application writes a `vendor_credit_applications` row and updates the
credit's `applied_amount` / `remaining_amount` / `status`.

### 6.4 Edge cases

- **`net = 0`** — still write a `PaymentRecord` with `amount = 0`; the PA still
  becomes `processed` with `paid_at` set. PA already has a zero-cash settlement
  precedent (`epms-api/app/models/pa.py:48`, `prepayment_applied`).
- **Credit larger than the payment** — partially consumed, stays `available`.
- **Credit fully consumed** — `exhausted`.
- **Currency** — must match exactly. No FX conversion. A CAD credit never applies
  to a USD PA.
- **Remittance advice** — must show `Gross / Credits applied / Net paid` and list
  the credit numbers (see `project_uniops_batch_payment_remittance`). A vendor
  receiving a short payment with no explanation will call.

### 6.5 GL posting

```
DR  accounts_payable              base
    CR  bank                              net
    CR  vendor_credit_clearing            credit_applied     ← new line_role
```

When `credit_applied = 0` the third line is omitted and the voucher is byte-for-byte
what it is today.

`vendor_credit_clearing` needs one row in `account_mappings`
(`mapping_type='line_role'`, `source_code='vendor_credit_clearing'`,
`account_code=<COA code>`). No new config key. Per the docstring on
`_stamp_account_codes` (`payment_execute.py:138`), a missing mapping leaves
`account_code` NULL and **does not block payment**, so the COA row can be
configured after deploy without risk.

Without this split the GL bank account would drift from the bank statement by the
applied amount on every credit application — unacceptable given the existing bank
reconciliation process.

### 6.6 Pre-existing inconsistency to resolve (decision point)

`payment_execute.py:309` sets `record.amount = req.amount_paid ?? pa.payment_amount`,
but the posting event at line 330 unconditionally uses `pa.payment_amount`. **Partial
payments already post a voucher that disagrees with the cash actually paid.** This
predates the credit feature; credits will make it fire more often.

**Decision: use `base` consistently in both.** This changes existing partial-payment
posting behaviour, so Finance must be told — if they have been manually adjusting
for the old (incorrect) figure, those adjustments must stop.

---

## 7. QBO opening-balance import

### 7.1 Source

`qbo_vendor_credits` already exists in the mirror (`finance-api/app/models/qbo.py:110`)
and inherits `_TxnHeaderMixin`, which carries `balance` — the **unapplied
remainder** in QBO.

```sql
SELECT qbo_id, doc_number, txn_date, currency, total_amt, balance,
       counterparty_id, counterparty_name
  FROM qbo_vendor_credits
 WHERE deleted_at IS NULL AND balance > 0
```

`total_amount` is taken from **`balance`, not `total_amt`**. `total_amt` is the
original face value; anything already applied inside QBO is history and must not
re-enter the pool. Original `total_amt` is retained in `notes` for reference.

### 7.2 Cutover date

Not a hand-entered date. Cutover = the most recent successful FULL RELOAD:

```sql
SELECT id, finished_at FROM qbo_sync_runs
 WHERE mode = 'full' AND status = 'success'      -- models/qbo.py:17 SUCCESS
 ORDER BY finished_at DESC LIMIT 1
```

Its id is stored on every imported row as `imported_from_sync_run_id` and shown on
the import page ("Opening balance as of the FULL RELOAD of <date>"). Re-running
the import after a later FULL RELOAD only adds new credits — the
`(source, source_ref)` unique index blocks duplicates.

### 7.3 Vendor mapping — the hard part

**Correction (2026-08-07): a QBO-vendor → `business_partners` matcher DOES exist on
`main`.** The earlier claim here — that the only such logic lived on the unmerged
`feature/qbo-vendor-remittance-email` branch — was wrong. `POST /qbo/vendor-emails/backfill`
(`api/v1/qbo.py`, shipped in `54c039e`) already matches on
`lower(trim(business_partners.name)) == lower(trim(qbo_vendors.display_name))`, and its
docstring states the same discipline this design settled on independently: *"ambiguous names
on either side are skipped and reported, never guessed."*

Phase C must **reuse that normalisation and its ambiguity handling** rather than invent a
second one. Note what it already gets right and must be preserved: it buckets BOTH sides by
the normalised key, and treats a key with more than one candidate on **either** side as
ambiguous — a one-directional lookup would silently pick the first of several same-named
partners. What Phase C adds on top is persistence of the human decision
(`qbo_vendor_map`), the employee/ignore classification, and the fact that a match only
pre-fills — it never auto-applies.

The QBO vendor list is a **superset** of EPMS vendors and mixes three populations:

| Class | Handling |
|---|---|
| A. Real supplier, exists in EPMS | Map to `business_partners.id`, import |
| B. Real supplier, **not** in EPMS | Listed, **not imported**. AP creates the vendor in Vendor Master, then re-runs the import (idempotent, picks up only the new ones). Vendor master data entry is not bypassed. |
| C. Employee set up as a vendor for expense reimbursement | Not a vendor credit. Marked `ignored_employee`. |

**No class is decided automatically.** Automatic matching is used **only to
pre-fill** the mapping control; every mapping requires an explicit human click
before `decision` is written. A mis-mapped vendor spends supplier A's money
against supplier B's account — the most expensive possible error here, and QBO's
mixed population makes the automatic error rate unknowable.

Pre-fill rule (narrow on purpose): normalised `qbo_vendors.display_name` exactly
equals normalised `business_partners.name`. Normalisation = casefold, collapse
whitespace, strip `Inc. / Ltd. / Corp. / , / .`.

### 7.4 Mapping workbench — Finance app, admin area

Placed next to the existing QBO sync UI: it is a one-off operational action, not
day-to-day AP work.

Four sections, each showing row count and total value:

1. **Ready to import** — `decision = 'mapped'`
2. **Needs mapping** — `decision = 'pending'`. Each row shows the QBO name, the
   credit balance, and advisory tags:
   - `Likely employee` — email domain is the company domain, or `display_name`
     matches an identity-api `users.full_name`
   - `No bills` — the vendor has no rows in `qbo_bills`
   - `Similar: <name>` — fuzzy match against an EPMS vendor, **suggestion only**

   Actions: `Map to…` (EPMS vendor search) / `Ignore — employee` / `Ignore — other`
3. **Ignored** — reversible
4. **Imported** — shows the allocated `VC-` number

### 7.5 Drift detection

On re-run, if a row already imported now has a different `balance` in QBO, it is
**not overwritten**. It is listed in a `Drifted` section for AP to inspect.

Overwriting could erase applications already recorded in EPMS. More importantly,
drift is the detector for the operating agreement below: if someone applied the
credit inside QBO after cutover, it shows up here.

### 7.6 Operating agreement (must go on the deploy checklist)

**After cutover, vendor credits are applied only in EPMS. No manual `apply` in
QBO.** Two independently-decrementing ledgers double-spend. §7.5 detects
violations; it does not prevent them.

---

## 8. Permissions, navigation, compatibility

- New permission `epms.vendor_credit.manage`, registered in
  `identity-api/scripts/seed_phase2_keys.py` — both the key→module map at line 20
  and the key→default-roles map at line 36. That script is the only registration
  point. **`verify_gate_parity.py` must NOT be edited**: its docstring declares it
  a one-shot acceptance tool holding a frozen, hand-typed snapshot of the 12
  phase-2 keys at cutover, deliberately not imported from the seed script; adding
  a key would corrupt the snapshot it exists to preserve.
  Default roles: `system_admin`, `ap_clerk`, `finance_manager`, `finance_bp`.
  The Portal Access Control matrix must then be ticked for the intended roles.
- **Uploading** a credit note reuses the existing invoice-upload permission — if
  you can upload an invoice, you can upload a credit note.
- **Approve / Reject / Void** and the import workbench require
  `epms.vendor_credit.manage`.
- Selecting credits at payment time reuses the existing `can_pay` authority; no
  new gate.
- **Self-review is allowed** — the uploader may approve their own credit. No SoD
  check is built. finance-api's `_sod_enabled` infrastructure
  (`payment_execute.py:90`) can be wired up later if the policy changes; building
  an unused switch now is not justified.
- EPMS sidebar entry goes through the single-source `navConfig`
  (`project_uniops_portal_sidebar`) — no per-page `NAV_SECTIONS`.
- **`invoices` table and `amount gt=0` are unchanged.** Keeping the constraint is
  deliberate: it is the last line of defence against a credit that was
  mis-classified as an invoice reaching the payment queue.
- The frontend's silent `return` on a negative amount becomes an explicit message
  pointing at the document-type switch.
- `expense-api`, two small changes: OCR returns `document_type`; the
  `invoice_source` whitelist (`api/v1/invoice_attachments.py:59`) accepts
  `"credit"`. No migration — the column is `String(10)`, and
  `api/v1/invoice_list.py` filters on explicit `"epms"` / `"oa"` values, so the
  unified list is unaffected.

---

## 9. Delivery phases

Each phase ships independently.

| Phase | Content | Why this order |
|---|---|---|
| **A** | Upload detection + confirmation + review + Vendor Credits page | Solves the reported problem (credit notes cannot be recorded) and **moves no money** |
| **B** | FIFO application at payment + GL clearing line + remittance display + §6.6 fix | First phase that touches cash; risk is concentrated here |
| **C** | QBO opening-balance import + vendor mapping workbench | One-off migration; only useful once B is live |

---

## 10. Testing

**finance-api (pytest)**

- A negative payload is stored positive; `CHECK (total_amount > 0)` holds
- Duplicate `(vendor_id, vendor_credit_number)` → 409
- `approve` / `reject` transitions; `void` refused when `applied_amount > 0`
- Two credits applied to one large PA (FIFO order respected)
- One credit spanning two payments (partial, stays `available`, then `exhausted`)
- Currency mismatch → not applied
- Credit ≥ base → `net = 0`, `PaymentRecord` still written, PA still `processed`
- `credit_ids = []` → nothing applied
- `credit_ids` naming an already-consumed credit → whole execution rolls back
- Two concurrent transactions on the same vendor → `SKIP LOCKED`, no double-spend
- Posting event balances: `DR ap = CR bank + CR vendor_credit_clearing`
- QBO import: `balance > 0` filter, `deleted_at` filter, idempotent re-run,
  unmapped vendors not imported, drift surfaced not overwritten

**Frontend** — `epms` tsc gate against the baseline of 59 errors
(`project_uniops_pa_chain_attachments`).

**Known traps (from project memory)**

- New alembic migration must hang `down_revision` off finance-api's real chain
  head (`feedback_uniops_alembic_new_migration_check_heads`)
- Only one test suite may run against the test DB at a time
  (`feedback_uniops_test_db_concurrency`)
- Running the epms frontend tsc gate inside a worktree needs `npm ci` there first
  (`project_uniops_gr_negative_discount_line`)
- PDF preview must use pdfjs + canvas, never an iframe
  (`feedback_uniops_pdf_preview_use_pdfjs`)

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Double-spend: credit applied in EPMS **and** in QBO | §7.6 operating agreement + §7.5 drift detection. Detection only — not prevention. |
| Mis-mapped QBO vendor spends the wrong vendor's credit | Every mapping requires an explicit human click; automatic matching only pre-fills (§7.3) |
| Vendor confused by a short payment | Remittance advice shows Gross / Credits applied / Net (§6.4) |
| §6.6 change alters existing partial-payment postings | Explicitly communicate to Finance before deploying Phase B |
| Credit note mis-classified as an invoice | `invoices.amount gt=0` retained as a backstop (§8) |
