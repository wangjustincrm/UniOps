# Fee-only Invoice → Link to PO (reference only)

**Date:** 2026-07-29
**Branch:** `feature/fee-only-invoice-link-po` (worktree `c:/Project/uniops-feeonly`, off `main` 1f2eb99)
**Migrations:** none

## Problem

A freight-only (or fee-only) vendor invoice has every line marked as a **non-PO fee**
("Mark as other fee"). The allocation panel balances (unallocated = 0), but on Confirm
the backend rejects it:

```python
# epms-api/app/crud/invoice.py  match()
allocs = await _normalize_allocations(invoice, req)
if not allocs:
    raise ValueError("At least one allocation is required")   # ← blocks fee-only invoices
```

The invoice can never leave the queue. The user wants such invoices to be confirmable
**and** associated with a PO for traceability.

## Decision (settled in brainstorming)

- **Semantics: reference/association only.** The freight is still paid in full via the
  AP header (照付), exactly like today's non-PO fee handling. Linking to a PO does **not**
  allocate the freight onto the PO, does **not** touch the PO's invoiced total or variance.
  Freight amount ≠ PO amount is expected and fine.
- **Mixed invoices unchanged.** When an invoice has real PO allocations *and* non-PO fee
  lines, the header PO is still derived from the allocations. `reference_po_id` only takes
  effect when there are **zero** allocations.
- **Link is mandatory** for a fee-only invoice: you must pick a PO to confirm.

## Why this is safe downstream (no silent underpayment)

`finance_sync.sync_ap_invoice` already pushes the AP header at the **full `total_amount`**
with status `posted` when matched, carrying `po_id`/`po_number`. A fee-only invoice therefore
becomes fully payable through AP. The known "silent underpayment" path
([[project_uniops_invoice_non_po_fee_lines]]) only affects **mixed** invoices where a PA
carves out the PO portion and `payment_execute` masks the fee — a fee-only invoice creates
**no PA at all**, so it is not exposed to that trap.

Invoice/PO linkage visibility is preserved: the invoice list & PO-detail filter already use
`or_(Invoice.po_id == po_id, <by allocation>)`, so setting `invoice.po_id` makes the fee-only
invoice appear under the linked PO's related invoices.

## Backend changes (epms-api)

### 1. Schema — `app/schemas/invoice.py`
Add to `InvoiceMatchRequest`:
```python
# When there are no PO allocations (fee-only invoice), the PO this invoice is
# associated with for traceability. Ignored when allocations are present.
reference_po_id: uuid.UUID | None = None
```

### 2. `match()` — `app/crud/invoice.py`
Replace the hard gate with a fee-only branch:

- Compute `allocs` and `excluded_total` as today.
- **If `allocs` is empty:**
  - Require the invoice to be fully covered by non-PO fees. The existing header balance
    check already enforces `alloc_total(0) + excluded_total == invoice.amount`; if it does
    not balance → `AllocationImbalance` (422). Keep that check running for this branch.
  - Require `req.reference_po_id`; else raise a new `FeeOnlyLinkRequired(ValueError)`
    ("Link a PO to confirm a fee-only invoice"). **Note:** a bare `ValueError` maps to **404**
    in the endpoint; only `AllocationImbalance`/`LegacyMatchUnsupported` map to 422. So
    `FeeOnlyLinkRequired` must be **added to the `except (...)` 422 tuple** in `match_invoice`
    (and imported) to return 422.
  - Load and validate the reference PO exists; if missing raise a plain
    `ValueError(f"Purchase order {id} not found")` → **404** (consistent with existing "PO not found").
  - Set header association from the reference PO:
    - `invoice.po_id = ref_po.id`, `invoice.po_number = ref_po.number`
    - `invoice.po_total = Decimal("0")`, `invoice.variance = Decimal("0")`, `invoice.variance_pct = Decimal("0")`
    - `invoice.matched_at/matched_by/matched_by_name` as today
    - `invoice.matched_po_line_ids = None`, `invoice.matched_reference_total = None`
  - Delete any stale `InvoicePoAllocation` rows for the invoice (idempotent re-match), create none.
  - GR handling: keep the existing block (a fee-only invoice normally has no GR; if one is
    passed it is recorded the same way).
  - Status: `matched` (no exception, no `match_review` — there are no PO lines to compare,
    so there is nothing to review; freight is simply paid via the AP header).
  - `flag_modified(invoice, "line_items")` still runs so the non-PO marks persist.
- **If `allocs` is non-empty:** unchanged. `reference_po_id` is ignored.

The `allocs[0]`-based rollup (lines ~388+) must be guarded so it only runs on the
non-empty path.

### 3. finance_sync — no change
Already syncs full amount + `po_id`. Verified sufficient.

## Frontend changes (epms)

### 1. `InvoiceAllocationPanel.tsx`
- Add a **"Link this invoice to a PO"** `<select>` at the top of the panel, populated
  **strictly from the existing `pos` prop** — the `match-candidates` list, already filtered
  server-side to `PurchaseOrder.vendor_id == invoice.vendor_id` + matchable statuses
  (`epms-api/app/api/v1/invoices.py:352`). **Do not add any new query that returns all POs**;
  the dropdown must only ever offer same-vendor candidate POs, identical to the drag-drop
  targets. New state `referencePoId`.
- It is relevant only when there are **no** allocations (`assignedTotal === 0` / `Object.keys(assign).length === 0`).
  When there are allocations, hide it (PO comes from allocations).
- Confirm enable rule:
  `balanced && (hasAllocations || referencePoId)`.
  When fee-only and no PO picked, disable Confirm with a hint
  ("Link a PO to confirm a fee-only invoice").
- Extend `onSubmit` payload to `{ allocations, nonPoLines, referencePoId }`.

### 2. Call sites & service contract
- `MatchPanel.tsx` and `InvoiceListPage` (the two `onSubmit` consumers): pass
  `reference_po_id` through to the match request.
- `services/invoices.ts`: add `reference_po_id?: string` to the match request type.

## Tests (epms-api `tests/test_invoice_allocations.py`)

- **Fee-only happy path:** invoice with all lines as `non_po_lines` covering the full pre-tax
  amount + `reference_po_id` → status `matched`, `invoice.po_id == ref_po`, zero allocations
  rows, `variance == 0`.
- **Missing link:** fee-only, no `reference_po_id` → 422.
- **Imbalanced:** fee-only whose excluded total ≠ amount → 422 (`AllocationImbalance`).
- **Regression:** existing multi-PO / mixed / legacy paths still pass unchanged.

Run per [[feedback_uniops_admin_test_db_env]] (worktree has no `.env` — pass
`JWT_SECRET_KEY` + local docker `POSTGRES_*`), serialized per [[feedback_uniops_test_db_concurrency]].
Baseline notes: `test_invoice_allocations.py` is a self-contained suite (uses `_make_issued_po`);
other invoice suites 502 locally is an environment gap, not a regression.

## Release discipline

Per [[feedback_uniops_multisession_release_discipline]]: own branch + worktree (done), commit
per logical unit, no WIP handoff. Zero migrations. Frontend tsc baseline 59 must not increase
([[project_uniops_pa_chain_attachments]]). Do **not** push/build/deploy without user approval;
merge at the single integration point when releasing.

## Out of scope

- Allocating freight as landed cost onto a PO (explicitly rejected in brainstorming).
- Changing mixed-invoice behavior.
- The broader "pay-through" fix for mixed invoices' PA/other_charges gap (separate project).
