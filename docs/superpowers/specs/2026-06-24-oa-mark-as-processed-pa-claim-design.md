# OA "Mark as Processed" for PA & Claim (match EPMS)

**Date:** 2026-06-24
**Status:** Approved, pending implementation
**Scope:** OA single-document payment (PA detail + Claim detail). Sub-project 1 of 2 (Batch Payment is a separate later sub-project). EPMS is NOT changed.

## Goal

Replace OA's "Record Payment" modal — on both the Direct PA detail page and the
expense Claim detail page — with the same "Mark as Processed" experience EPMS
uses: a single **"Pay from (bank account/credit card)"** dropdown. Rename the
button + modal title to **"Mark as Processed"** (EPMS's existing wording).

## Reference (EPMS, do not change)

`epms/src/pages/pa/PaDetailPage.tsx` `ProcessModal`: title "Mark as Processed",
subtitle `{paNumber} · {amount} {currency}`, one dropdown "Pay from (bank account
or credit card) *" populated from `financeApi.get('/bank/accounts')` filtered to
`is_active && currency === <doc currency>`, an empty-state hint, Cancel +
"Confirm Payment". It submits a `bank_account_id`; the shared finance executor
defaults payment date to today and amount to the document total.

## Current OA state

- PA: `oa/src/pages/pa/PaDetailPage.tsx` `PaymentModal` (Payment Date / Bank Ref /
  Amount / Notes) → `handlePay` → `POST /api/v1/pa/{id}/pay` with
  `PaymentRecord {payment_date, bank_reference, amount_paid, notes}`
  (`expense-api/app/schemas/pa.py`). `record_payment` forwards to
  `finance_client.execute_payment(... payment_date, reference, amount_paid, notes)`
  — **no `bank_account_id`**.
- Claim: `oa/src/pages/expenses/ExpenseDetailPage.tsx` has an identical
  `PaymentModal`/`PaymentInput` → `POST /api/v1/expenses/{id}/pay` with
  `PaymentRecordRequest {payment_date, bank_reference, amount_paid, notes}`
  (`expense-api/app/schemas/expense.py`). `record_payment` forwards with
  `doc_kind="expense_claim"`.
- OA has no finance-api client. finance-api CORS already allows OA's origin
  (`http://localhost:5175`).
- The shared executor (`finance-api` `/finance/v1/payments/execute`,
  `PaymentExecuteRequest`) already accepts `bank_account_id` (and defaults
  `payment_date`=today, `amount_paid`=document total).

## Design

### Frontend

1. **`oa/src/lib/api.ts` — add a `financeApi` client** mirroring EPMS:
   `FINANCE_BASE = VITE_FINANCE_API_URL || 'http://localhost:8004'`; `get(path)`
   fetches `${FINANCE_BASE}/finance/v1${path}` with the bearer header (same token
   handling as the other clients). Export `financeApi.get`.

2. **New shared component `oa/src/components/ProcessPaymentModal.tsx`** (one unit,
   used by both detail pages — avoids duplicating the bank-account fetch):
   - Props: `docNumber: string`, `currency: string`, `amount: number`,
     `busy: boolean`, `onConfirm: (bankAccountId: string) => void`, `onClose: () => void`.
   - Fetches `financeApi.get<FundingAccount[]>('/bank/accounts')`
     (`FundingAccount = { id, name, bank_name, kind, account_masked, currency, is_active }`),
     filters `is_active && currency === currency`.
   - Renders the EPMS layout: title "Mark as Processed", subtitle
     `{docNumber} · {formatAmount(amount, currency)}`, the single "Pay from (bank
     account/credit card) *" dropdown (same option formatting and empty-state
     hint), Cancel + "Confirm Payment" (disabled until a bank is selected or while
     `busy`). On confirm calls `onConfirm(bankId)`.

3. **`oa/src/pages/pa/PaDetailPage.tsx`:**
   - Remove `PaymentModal` + `PaymentInput`. Render `ProcessPaymentModal` for
     `activeModal === 'pay'` with `docNumber={pa.pa_number}`, `currency={pa.currency}`,
     `amount={Number(pa.payment_amount)}`, `busy={acting}`, `onConfirm={handlePay}`.
   - `handlePay` becomes `(bankAccountId: string) => api.post('/api/v1/pa/{id}/pay', { bank_account_id: bankAccountId })`.
   - Rename the trigger button label "Record Payment" → "Mark as Processed"
     (keep the existing `can_pay` gating; use the `CreditCard` icon).

4. **`oa/src/pages/expenses/ExpenseDetailPage.tsx`:** the same changes —
   remove its `PaymentModal`/`PaymentInput`, render `ProcessPaymentModal`
   (`docNumber={claim.claim_number}`, `currency={claim.currency}`,
   `amount={Number(claim.total_amount)}` — the same value the current modal's
   `defaultAmount` uses), rename "Record Payment" → "Mark as Processed", and
   change the pay mutation to post `{ bank_account_id }`.

### Backend (`expense-api`)

5. **`schemas/pa.py`** — `PaymentRecord` → `{ bank_account_id: uuid.UUID }`
   (replaces payment_date/bank_reference/amount_paid/notes).
6. **`schemas/expense.py`** — `PaymentRecordRequest` → `{ bank_account_id: uuid.UUID }`.
7. **`services/finance_client.py`** — `execute_payment` gains
   `bank_account_id: uuid.UUID | None = None`; when set, add
   `payload["bank_account_id"] = str(bank_account_id)` (mirror EPMS's finance_client).
8. **`api/v1/pa.py`** `record_payment` — call
   `finance_client.execute_payment(doc_kind=..., doc_id=pa_id, bearer_token=token, bank_account_id=body.bank_account_id)`.
9. **`api/v1/expenses.py`** `record_payment` — call
   `finance_client.execute_payment(doc_kind="expense_claim", doc_id=claim_id, bearer_token=token, bank_account_id=body.bank_account_id)`.
10. **Tests** — update the existing payment tests to the new `{bank_account_id}`
    contract: in `tests/test_pa.py` the 3 `record_payment` tests (409/403/forward)
    now post `{"bank_account_id": "<uuid>"}` and the forward test asserts
    `kw["bank_account_id"]` is passed; update any claim payment test similarly if
    one exists (search `tests/` for `/pay`).

## Out of scope

- Batch Payment (sub-project 2).
- EPMS (its "Mark as Processed" already exists — unchanged).
- The `/pay` route names and the `can_pay` permission gating (unchanged).
- Prepayment/settlement flows.

## Verification

- Backend: `pytest tests/test_pa.py -k "payment or pay" -v` and the claim payment
  test(s) pass against the local test DB; full `tests/test_pa.py` green.
- Frontend: `tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` in `oa/`.
- Manual: on an approved PA and an approved Claim, the action button reads "Mark
  as Processed"; the modal shows the single bank-account dropdown (filtered to the
  doc currency); confirming pays via the executor and the doc flips to processed/paid.

## Constraints

- UI strings English-only.
- No git commits this round — working tree only.
- Production must set `VITE_FINANCE_API_URL` for OA (dev defaults to :8004; CORS already allows OA origin).
