# OA "Mark as Processed" (PA + Claim) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace OA's "Record Payment" modal (PA and Claim detail pages) with EPMS's "Mark as Processed" experience — a single bank-account dropdown that submits `bank_account_id` to the shared finance executor.

**Architecture:** Backend: OA's two `/pay` endpoints switch their request body to `{ bank_account_id }` and forward it through `finance_client.execute_payment` (the executor already defaults date=today, amount=full). Frontend: a new OA `financeApi` client + one shared `ProcessPaymentModal` component, wired into both detail pages; the trigger buttons are renamed "Mark as Processed".

**Tech Stack:** FastAPI + SQLAlchemy async + pytest (expense-api); React + TypeScript 6.0.3 + @tanstack/react-query (oa).

## Global Constraints

- No git commits / pushes / branches this round — changes stay in the working tree.
- UI strings English-only.
- Frontend typecheck (run in `uniops/oa/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`.
- Backend tests run against the `expense_test` DB per the project convention (conftest derives `expense_test`; reach the local docker Postgres via the expense-api DB env). See memory `feedback_uniops_admin_test_db_env`.
- EPMS is NOT modified. The label is exactly "Mark as Processed".
- Match EPMS exactly: single "Pay from (bank account/credit card)" dropdown; no Date/EFT/Amount/Notes fields.

---

### Task 1: Backend — `/pay` accepts `bank_account_id` (PA + Claim)

**Files:**
- Modify: `uniops/expense-api/app/schemas/pa.py`
- Modify: `uniops/expense-api/app/schemas/expense.py`
- Modify: `uniops/expense-api/app/services/finance_client.py`
- Modify: `uniops/expense-api/app/api/v1/pa.py`
- Modify: `uniops/expense-api/app/api/v1/expenses.py`
- Test: `uniops/expense-api/tests/test_pa.py`

**Interfaces:**
- Produces: `PaymentRecord { bank_account_id: uuid.UUID }`, `PaymentRecordRequest { bank_account_id: uuid.UUID }`; `finance_client.execute_payment(..., bank_account_id=)`. Both `/pay` endpoints now accept `{ "bank_account_id": "<uuid>" }`.

- [ ] **Step 1: Update the 3 PA payment tests to the new contract**

In `uniops/expense-api/tests/test_pa.py`, replace the three `record_payment` tests' bodies. The 409 test:

```python
@pytest.mark.asyncio
async def test_record_payment_maps_409_from_finance(admin_client, mocker):
    mock_exec = _mock_finance(mocker, side_effect=ValueError("Cannot pay PA in status 'draft'"))
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()

    resp = await admin_client.post(
        f"/api/v1/pa/{pa['id']}/pay",
        json={"bank_account_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 409
    mock_exec.assert_called_once()
```

The 403 test:

```python
@pytest.mark.asyncio
async def test_record_payment_maps_403_from_finance(admin_client, mocker):
    _mock_finance(mocker, side_effect=PermissionError("Insufficient role"))
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()

    resp = await admin_client.post(
        f"/api/v1/pa/{pa['id']}/pay",
        json={"bank_account_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 403
```

The forward test (now asserts `bank_account_id` is forwarded):

```python
@pytest.mark.asyncio
async def test_record_payment_forwards_to_finance(admin_client, mocker):
    """The forward carries doc_kind=pa_dir and the chosen bank_account_id."""
    mock_exec = _mock_finance(mocker, return_value={"new_status": "processed"})
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()

    bank_id = str(uuid.uuid4())
    resp = await admin_client.post(
        f"/api/v1/pa/{pa['id']}/pay",
        json={"bank_account_id": bank_id},
    )
    assert resp.status_code == 200
    mock_exec.assert_called_once()
    kw = mock_exec.call_args.kwargs
    assert kw["doc_kind"] == "pa_dir"
    assert str(kw["doc_id"]) == pa["id"]
    assert str(kw["bank_account_id"]) == bank_id
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (in `uniops/expense-api/`, test DB configured): `pytest tests/test_pa.py -k record_payment -v`
Expected: FAIL — the endpoint still expects the old fields, so the new body yields 422 (or the forward assertion on `bank_account_id` fails).

- [ ] **Step 3: Update `PaymentRecord` schema (PA)**

In `uniops/expense-api/app/schemas/pa.py`, replace the `PaymentRecord` class:

```python
class PaymentRecord(BaseModel):
    payment_date: str          # YYYY-MM-DD
    bank_reference: str
    amount_paid: Decimal
    notes: str | None = None
```

with:

```python
class PaymentRecord(BaseModel):
    bank_account_id: uuid.UUID   # funding bank/card chosen in the modal
```

(`uuid` is already imported in this file; `Decimal` stays used by `PaResponse`.)

- [ ] **Step 4: Update `PaymentRecordRequest` schema (Claim)**

In `uniops/expense-api/app/schemas/expense.py`, replace the `PaymentRecordRequest` class:

```python
class PaymentRecordRequest(BaseModel):
    payment_date: date
    bank_reference: str
    amount_paid: Decimal
    notes: Optional[str] = None
```

with:

```python
class PaymentRecordRequest(BaseModel):
    bank_account_id: uuid.UUID   # funding bank/card chosen in the modal
```

Ensure `import uuid` is present at the top of `schemas/expense.py` (add it if missing). Leave `date`/`Decimal`/`Optional` imports — they are used by other schemas in this file.

- [ ] **Step 5: Add `bank_account_id` to `finance_client.execute_payment`**

In `uniops/expense-api/app/services/finance_client.py`, add the parameter (after `notes`):

```python
    amount_paid: Decimal | None = None,
    notes: str | None = None,
    bank_account_id: uuid.UUID | None = None,
) -> dict[str, Any]:
```

and add to the payload (after the `notes` block):

```python
    if notes is not None:
        payload["notes"] = notes
    if bank_account_id is not None:
        payload["bank_account_id"] = str(bank_account_id)
```

- [ ] **Step 6: Forward it from the PA endpoint**

In `uniops/expense-api/app/api/v1/pa.py`, in `record_payment`, replace the `execute_payment` call:

```python
        await finance_client.execute_payment(
            doc_kind="pa_dir" if pa.po_id is None else "pa",
            doc_id=pa_id, bearer_token=token,
            payment_date=body.payment_date, reference=body.bank_reference,
            amount_paid=body.amount_paid, notes=body.notes,
        )
```

with:

```python
        await finance_client.execute_payment(
            doc_kind="pa_dir" if pa.po_id is None else "pa",
            doc_id=pa_id, bearer_token=token,
            bank_account_id=body.bank_account_id,
        )
```

- [ ] **Step 7: Forward it from the Claim endpoint**

In `uniops/expense-api/app/api/v1/expenses.py`, in `record_payment`, replace the `execute_payment` call:

```python
        await finance_client.execute_payment(
            doc_kind="expense_claim", doc_id=claim_id, bearer_token=token,
            payment_date=body.payment_date, reference=body.bank_reference,
            amount_paid=body.amount_paid, notes=body.notes,
        )
```

with:

```python
        await finance_client.execute_payment(
            doc_kind="expense_claim", doc_id=claim_id, bearer_token=token,
            bank_account_id=body.bank_account_id,
        )
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_pa.py -k record_payment -v` → 3 PASS.
Then `pytest tests/test_pa.py -v` → all PASS (no regressions).

- [ ] **Step 9: (No commit — leave changes in the working tree.)**

---

### Task 2: Frontend foundation — `financeApi` client + shared `ProcessPaymentModal`

**Files:**
- Modify: `uniops/oa/src/lib/api.ts`
- Create: `uniops/oa/src/components/ProcessPaymentModal.tsx`

**Interfaces:**
- Produces: `financeApi.get<T>(path)` (calls finance-api `:8004` `/finance/v1<path>`); `ProcessPaymentModal` default export with props `{ docNumber: string; currency: string; amount: number; busy: boolean; onConfirm: (bankAccountId: string) => void; onClose: () => void }`.

- [ ] **Step 1: Add the `financeApi` client**

In `uniops/oa/src/lib/api.ts`, add a base const next to the other `*_BASE` consts (after the `MDM_BASE` line):

```ts
const FINANCE_BASE = (import.meta.env.VITE_FINANCE_API_URL as string | undefined) || 'http://localhost:8004'
```

And add a client near the `mdmApi` block:

```ts
// finance-api (:8004) — bank/card accounts for the payment-source picker
async function financeRequest<T>(path: string): Promise<T> {
  const h: Record<string, string> = {}
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${FINANCE_BASE}/finance/v1${path}`, { headers: h })
  if (!res.ok) throw await toApiError(res)
  return res.json()
}

export const financeApi = { get: <T>(path: string) => financeRequest<T>(path) }
```

- [ ] **Step 2: Create the shared modal**

Create `uniops/oa/src/components/ProcessPaymentModal.tsx`:

```tsx
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { CreditCard, CheckCircle2, X } from 'lucide-react'
import { formatAmount } from '@/lib/utils'
import { financeApi } from '@/lib/api'

interface FundingAccount {
  id: string; name: string; bank_name: string; kind: string
  account_masked: string | null; currency: string; is_active: boolean
}

export default function ProcessPaymentModal({
  docNumber, currency, amount, busy, onConfirm, onClose,
}: {
  docNumber: string; currency: string; amount: number; busy: boolean
  onConfirm: (bankAccountId: string) => void; onClose: () => void
}) {
  const [bankId, setBankId] = useState('')
  const { data: accounts = [], isLoading } = useQuery({
    queryKey: ['finance-bank-accounts'],
    queryFn: () => financeApi.get<FundingAccount[]>('/bank/accounts'),
  })
  const options = accounts.filter((a) => a.is_active && a.currency === currency)

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary-50">
              <CreditCard className="h-5 w-5 text-primary-600" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">Mark as Processed</h2>
              <p className="text-xs text-neutral-500">{docNumber} · {formatAmount(amount, currency)}</p>
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100"><X className="h-4 w-4" /></button>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Pay from (bank account or credit card) *</label>
            <select value={bankId} onChange={(e) => setBankId(e.target.value)}
              className="w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600">
              <option value="">Select a {currency} payment source…</option>
              {options.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.kind === 'credit_card' ? '💳' : '🏦'} {a.bank_name} — {a.name}{a.account_masked ? ` · …${a.account_masked}` : ''}
                </option>
              ))}
            </select>
            {!isLoading && options.length === 0 && (
              <p className="text-xs text-amber-600">No active {currency} accounts. Add one under Finance → Bank &amp; Cards.</p>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-neutral-200 px-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50">Cancel</button>
            <button disabled={!bankId || busy} onClick={() => onConfirm(bankId)}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-40 disabled:cursor-not-allowed">
              <CheckCircle2 className="h-4 w-4" />{busy ? 'Processing…' : 'Confirm Payment'}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
```

- [ ] **Step 3: Typecheck**

Run (in `uniops/oa/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: PASS, no errors.

---

### Task 3: Frontend wiring — PA + Claim detail pages

**Files:**
- Modify: `uniops/oa/src/pages/pa/PaDetailPage.tsx`
- Modify: `uniops/oa/src/pages/expenses/ExpenseDetailPage.tsx`

**Interfaces:**
- Consumes: `ProcessPaymentModal` (Task 2), the `/pay` endpoints now expecting `{ bank_account_id }` (Task 1).

- [ ] **Step 1: PA — import the modal + CreditCard, drop the old modal**

In `uniops/oa/src/pages/pa/PaDetailPage.tsx`:
- Add `import ProcessPaymentModal from '@/components/ProcessPaymentModal'` (with the other imports).
- In the lucide-react import, remove `Banknote` and add `CreditCard` (Banknote was only used by the old payment button).
- Delete the `PaymentInput` interface and the entire `PaymentModal` function component (no longer used).

- [ ] **Step 2: PA — rename the button and switch `handlePay`**

Change the trigger button in `ActionArea` (the `status === 'approved' && canPay` branch) from:

```tsx
        <button onClick={() => onOpenModal('pay')} disabled={acting}
          className="flex items-center gap-2 self-start rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50 transition-colors">
          <Banknote className="h-4 w-4" />Record Payment
        </button>
```

to:

```tsx
        <button onClick={() => onOpenModal('pay')} disabled={acting}
          className="flex items-center gap-2 self-start rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50 transition-colors">
          <CreditCard className="h-4 w-4" />Mark as Processed
        </button>
```

Change `handlePay` to accept a bank id:

```tsx
  const handlePay = async (bankAccountId: string) => {
    if (!id) return
    setActing(true); setActError('')
    try {
      await api.post(`/api/v1/pa/${id}/pay`, { bank_account_id: bankAccountId })
      setActiveModal(null)
      await invalidateAll()
    } catch (e: any) {
      setActError(e.message || 'Failed to record payment')
    } finally {
      setActing(false)
    }
  }
```

- [ ] **Step 3: PA — swap the modal render**

Replace the `activeModal === 'pay'` render block:

```tsx
      {/* Record Payment modal */}
      {activeModal === 'pay' && (
        <PaymentModal
          defaultAmount={Number(pa.payment_amount)}
          currency={pa.currency}
          loading={acting}
          error={actError}
          onClose={() => setActiveModal(null)}
          onConfirm={handlePay}
        />
      )}
```

with:

```tsx
      {/* Mark as Processed modal */}
      {activeModal === 'pay' && (
        <ProcessPaymentModal
          docNumber={pa.pa_number}
          currency={pa.currency}
          amount={Number(pa.payment_amount)}
          busy={acting}
          onConfirm={handlePay}
          onClose={() => setActiveModal(null)}
        />
      )}
```

- [ ] **Step 4: Claim — import the modal, drop the old one, rename button**

In `uniops/oa/src/pages/expenses/ExpenseDetailPage.tsx`:
- Add `import ProcessPaymentModal from '@/components/ProcessPaymentModal'`.
- Delete the `PaymentInput` interface and the entire `PaymentModal` function component.
- Rename the trigger button text "Record Payment" → "Mark as Processed":

```tsx
            {claim.status === 'approved' && canPay && (
              <button
                onClick={() => setActiveAction('pay')}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700"
              >
                Mark as Processed
              </button>
            )}
```

- [ ] **Step 5: Claim — switch the mutation and the modal render**

Change `payMutation`'s `mutationFn`:

```tsx
  const payMutation = useMutation({
    mutationFn: (bankAccountId: string) => api.post(`/api/v1/expenses/${id}/pay`, { bank_account_id: bankAccountId }),
    onSuccess: () => { setActiveAction(null); invalidateAll() },
  })
```

Replace the payment modal render block:

```tsx
      {/* Payment modal (dedicated /pay endpoint with payment fields) */}
      {activeAction === 'pay' && (
        <PaymentModal
          defaultAmount={Number(claim.total_amount)}
          currency={claim.currency}
          loading={payMutation.isPending}
          error={payMutation.isError ? (payMutation.error as Error).message : null}
          onClose={() => setActiveAction(null)}
          onConfirm={(body) => payMutation.mutate(body)}
        />
      )}
```

with:

```tsx
      {/* Mark as Processed modal */}
      {activeAction === 'pay' && (
        <ProcessPaymentModal
          docNumber={claim.claim_number}
          currency={claim.currency}
          amount={Number(claim.total_amount)}
          busy={payMutation.isPending}
          onConfirm={(bankAccountId) => payMutation.mutate(bankAccountId)}
          onClose={() => setActiveAction(null)}
        />
      )}
```

- [ ] **Step 6: Typecheck and clean unused imports**

Run (in `uniops/oa/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: PASS. If the typecheck reports any now-unused import (e.g. `Banknote`, or an icon only the deleted `PaymentModal` used such as `AlertTriangle` in ExpenseDetailPage IF it is unused elsewhere), remove only those flagged symbols from the import and re-run until clean. Do not remove imports still referenced elsewhere in the file.

- [ ] **Step 7: Manual verification**

On an approved PA and an approved Claim: the action button reads "Mark as Processed"; clicking it opens the EPMS-style modal with the single "Pay from (bank account/credit card)" dropdown filtered to the document's currency; selecting an account and confirming pays via the executor and the document flips to processed/paid.

---

## Notes

- No commit steps: leave changes in the working tree for the batch commit.
- Task order: Task 1 (backend contract) and Task 2 (frontend foundation) are independent; Task 3 depends on both.
- EPMS untouched. Batch Payment (claims + combined payment) is sub-project 2, brainstormed separately.
