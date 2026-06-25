# Batch Payment — include Claims (Sub-project 2)

**Date:** 2026-06-24
**Status:** Approved, pending implementation
**Scope:** finance-api batch logic + the Portal Payment Batch page. Sub-project 2 of 2 (sub-project 1 = single-doc Mark as Processed, already done).

## Goal

The Payment Batch workbench should let finance select and pay **both approved PAs
and approved expense Claims** in a single batch run (the existing "multi-doc, one
bank account, one execution" model — *combined payment*). After execution each
document's status flips automatically (PA → `processed`, Claim → `paid`), which
the unified executor already does per line.

## Decisions (from brainstorming)

- "Combined payment" = select multiple PAs + Claims into one batch and execute
  them together from one bank account; each doc keeps its own `payment_record`.
  **No** same-payee transaction merging, **no** executor/payment-record redesign.
- Claim terminal status stays `paid` (PA stays `processed`); no claim-lifecycle change.

## Current state (verified)

- `finance-api/app/crud/payment_batch.py`:
  - `list_due(db, currency)` returns approved **PAs only** as
    `{doc_kind, doc_id, doc_number, vendor_name, amount, currency}`.
  - `create_batch(db, doc_ids, ...)` loads only `PaymentApplication`, validates
    found/approved/single-currency, snapshots PA lines (`doc_kind pa|pa_dir`).
  - `execute_batch` runs each line through `payment_execute.execute` by
    `ln.doc_kind` with per-line savepoint isolation; the executor flips each doc's
    status and writes a `payment_record` tagged with `batch_id`.
- `finance-api/app/api/v1/payments.py`: `CreateBatchRequest { doc_ids: list[uuid] }`;
  `/due` returns `list[dict]`; `/batches` create + `/batches/{id}/execute`.
- `finance-api/app/crud/payment_execute.py` already handles `doc_kind == "expense_claim"`
  (loads `ExpenseClaim`, requires `status == "approved"`, sets `status = "paid"`,
  records the payment). The `ExpenseClaim` mirror (`app/models/mirrors.py`) has
  `claim_number, employee_name, employee_id, currency, total_amount, status, claim_type`.
- Portal `portal/src/pages/finance/PaymentBatchPage.tsx`: due list (PAs), select →
  create batch (`{ doc_ids }`), open batch → pick bank → execute.
- Tests: `finance-api/tests/test_payment_batch.py`
  (`test_due_lists_approved_pas`, `test_create_and_execute_batch`,
  `test_create_batch_rejects_mixed_currency`, `test_batch_isolates_line_failure`).

## Design

### finance-api

1. **`crud/payment_batch.py`**
   - Import `ExpenseClaim` from `app.models.mirrors`.
   - `list_due`: after the PA query, also query approved claims
     (`select(ExpenseClaim).where(status == "approved")`, plus `currency` filter when
     given) and append rows
     `{doc_kind: "expense_claim", doc_id: str(id), doc_number: claim_number,
       payee: employee_name, amount: str(total_amount), currency}`.
     Change the PA rows to the same shape using `payee: vendor_name` (replace the
     `vendor_name` key with `payee`). Return PAs then claims.
   - `create_batch(db, *, docs, payment_method, batch_date, created_by)`:
     `docs` is a list of `(doc_kind, doc_id)`. Partition into PA ids
     (`doc_kind in ("pa", "pa_dir")`) and claim ids (`doc_kind == "expense_claim"`).
     Load `PaymentApplication` for PA ids and `ExpenseClaim` for claim ids. Validate:
     non-empty selection; every requested id found; every doc `status == "approved"`
     (error lists the offending numbers); single currency across the **combined** set.
     Create the batch (`total` = sum of PA `payment_amount` + claim `total_amount`),
     then snapshot lines: PA lines as today; claim lines
     (`doc_kind="expense_claim", doc_number=claim_number, amount=total_amount`).
   - `execute_batch`: **no change**.

2. **`api/v1/payments.py`**
   - Add `class DocRef(BaseModel): doc_kind: str; doc_id: uuid.UUID`.
   - `CreateBatchRequest`: replace `doc_ids: list[uuid.UUID]` with `docs: list[DocRef]`.
   - `create_batch` endpoint: pass `docs=[(d.doc_kind, d.doc_id) for d in body.docs]`
     to the crud.
   - `/due` unchanged in signature (returns the now-richer `list[dict]`).

3. **`tests/test_payment_batch.py`**
   - Update existing `create_batch` calls / requests to the new `docs` shape.
   - Add an approved `ExpenseClaim` row in the setup and assert `list_due` includes it
     (doc_kind `expense_claim`, `payee` set).
   - Add a test that creates a batch containing a claim line and executes it, asserting
     the line is `paid` and the claim's `status` becomes `paid`.
   - Keep `test_create_batch_rejects_mixed_currency` and
     `test_batch_isolates_line_failure` green under the new shape.

### Portal UI — `PaymentBatchPage.tsx`

- `Due` interface: rename `vendor_name` → `payee`.
- Due table header "Vendor" → "Payee"; render `d.payee`. Type column:
  `doc_kind === 'expense_claim' ? 'Claim' : doc_kind === 'pa_dir' ? 'Direct PA' : 'PA'`.
- `createBatch` mutation body: `{ docs: [...selected].map((id) => { const d = due.find((x) => x.doc_id === id)!; return { doc_kind: d.doc_kind, doc_id: id } }), payment_method: 'bank_transfer' }`.
- `BatchLine` rows in `BatchDetailModal`: add a small Type cell using the same
  `doc_kind` label mapping.

### Unchanged
- Unified executor, batch model (`doc_kind` is a free string), the funding-bank
  selection + single-currency batch rule, and the auto status-flip on execute.

## Verification

- Backend: `pytest tests/test_payment_batch.py -v` (incl. the new claim cases) green;
  full finance-api suite green. Run against the finance-api test DB per convention.
- Frontend: in `portal/`, `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` (same TS 6.0.3 toolchain as the other frontends; `portal/tsconfig.app.json` exists).
- Manual: with an approved PA and an approved Claim of the same currency, both appear
  in "Due for payment"; selecting both → Create Batch → pick bank → Execute marks both
  lines paid; the PA shows `processed` and the Claim shows `paid`.

## Constraints

- UI strings English-only.
- No git commits this round — working tree only.
- finance-api `create_batch` request contract changes (`doc_ids` → `docs`); the only
  caller is the Portal page, updated in the same change.
