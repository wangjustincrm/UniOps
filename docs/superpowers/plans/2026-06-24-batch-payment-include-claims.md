# Batch Payment — include Claims Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Payment Batch workbench select and pay approved expense Claims alongside approved PAs in one batch run.

**Architecture:** finance-api's `list_due` and `create_batch` gain claim support (the executor + `execute_batch` already handle `doc_kind="expense_claim"` and the status flip); the batch-create request switches from bare ids to `{doc_kind, doc_id}` refs. The Portal Payment Batch page lists claims with a unified "Payee" column and sends the new ref shape.

**Tech Stack:** FastAPI + SQLAlchemy async + pytest (finance-api); React + TypeScript 6.0.3 (portal).

## Global Constraints

- No git commits / pushes / branches this round — changes stay in the working tree.
- UI strings English-only.
- finance-api tests run against the finance-api test DB per project convention.
- Portal typecheck (run in `portal/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`.
- Combined payment = multi-doc one run (no same-payee merge). Claim terminal status stays `paid`; PA stays `processed`. The executor and `execute_batch` are NOT changed.

---

### Task 1: finance-api — claims in `list_due` + `create_batch`

**Files:**
- Modify: `uniops/finance-api/app/crud/payment_batch.py`
- Modify: `uniops/finance-api/app/api/v1/payments.py`
- Test: `uniops/finance-api/tests/test_payment_batch.py`

**Interfaces:**
- Produces: `list_due` rows now use `payee` (not `vendor_name`) and include `expense_claim` rows; `create_batch(db, *, docs, payment_method, batch_date, created_by)` where `docs: list[tuple[str, uuid.UUID]]`; `POST /payments/batches` body is `{ docs: [{doc_kind, doc_id}], payment_method?, batch_date? }`.

- [ ] **Step 1: Add the claim test helper + update existing tests to the `docs` shape**

In `uniops/finance-api/tests/test_payment_batch.py`, add the `ExpenseClaim` import and a `_claim` helper near `_pa`:

```python
from app.models.mirrors import ExpenseClaim
```

```python
def _claim(amount="100.00", status="approved", currency="CAD") -> ExpenseClaim:
    return ExpenseClaim(
        claim_number=f"EXP-{uuid.uuid4().hex[:8]}", claim_type="EXP", status=status,
        employee_id=uuid.uuid4(), employee_name="Jane Doe", currency=currency,
        total_amount=Decimal(amount), tax_amount=Decimal("0"), net_amount=Decimal(amount),
    )
```

Update the three existing tests that POST `doc_ids` to send `docs` instead.
`test_create_and_execute_batch`:

```python
    r = await client.post("/finance/v1/payments/batches", headers=_h(),
                          json={"docs": [{"doc_kind": "pa_dir", "doc_id": str(a.id)},
                                         {"doc_kind": "pa_dir", "doc_id": str(b.id)}]})
```

`test_create_batch_rejects_mixed_currency`:

```python
    r = await client.post("/finance/v1/payments/batches", headers=_h(),
                          json={"docs": [{"doc_kind": "pa_dir", "doc_id": str(a.id)},
                                         {"doc_kind": "pa_dir", "doc_id": str(b.id)}]})
```

`test_batch_isolates_line_failure`:

```python
    r = await client.post("/finance/v1/payments/batches", headers=_h(),
                          json={"docs": [{"doc_kind": "pa_dir", "doc_id": str(good.id)}]})
```

- [ ] **Step 2: Add the new claim tests**

Append to `uniops/finance-api/tests/test_payment_batch.py`:

```python
async def test_due_includes_approved_claims(client, db_session):
    db_session.add_all([_pa("100.00"), _claim("75.00")])
    await db_session.flush()
    r = await client.get("/finance/v1/payments/due", headers=_h())
    assert r.status_code == 200
    rows = r.json()
    claim_rows = [x for x in rows if x["doc_kind"] == "expense_claim"]
    assert len(claim_rows) == 1
    assert claim_rows[0]["payee"] == "Jane Doe"
    assert all("payee" in x for x in rows)


async def test_batch_with_claim_executes_and_flips_status(client, db_session):
    pa, claim = _pa("100.00"), _claim("75.00")
    db_session.add_all([pa, claim])
    await db_session.flush()

    r = await client.post("/finance/v1/payments/batches", headers=_h(),
                          json={"docs": [{"doc_kind": "pa_dir", "doc_id": str(pa.id)},
                                         {"doc_kind": "expense_claim", "doc_id": str(claim.id)}]})
    assert r.status_code == 201, r.text
    assert r.json()["total"] == "175.00"

    r2 = await client.post(f"/finance/v1/payments/batches/{r.json()['id']}/execute",
                           headers=_h("ap_clerk"))
    assert r2.status_code == 200, r2.text
    assert r2.json()["paid"] == 2 and r2.json()["failed"] == 0

    await db_session.refresh(pa); await db_session.refresh(claim)
    assert pa.status == "processed"
    assert claim.status == "paid"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run (in `uniops/finance-api/`, test DB configured): `pytest tests/test_payment_batch.py -v`
Expected: FAIL — the new `docs` body is rejected (422) because the endpoint still expects `doc_ids`, and `list_due` returns no claims / no `payee` key.

- [ ] **Step 4: Add claims to `list_due` and `create_batch`**

In `uniops/finance-api/app/crud/payment_batch.py`, add the import:

```python
from app.models.mirrors import ExpenseClaim
```

Replace `list_due`:

```python
async def list_due(db: AsyncSession, currency: str | None = None) -> list[dict]:
    """Approved PAs and approved expense claims awaiting payment — pickable rows."""
    pq = select(PaymentApplication).where(PaymentApplication.status == "approved")
    if currency:
        pq = pq.where(PaymentApplication.currency == currency)
    pas = (await db.execute(pq.order_by(PaymentApplication.submitted_at))).scalars().all()

    cq = select(ExpenseClaim).where(ExpenseClaim.status == "approved")
    if currency:
        cq = cq.where(ExpenseClaim.currency == currency)
    claims = (await db.execute(cq.order_by(ExpenseClaim.created_at))).scalars().all()

    rows = [
        {"doc_kind": "pa_dir" if r.po_id is None else "pa",
         "doc_id": str(r.id), "doc_number": r.pa_number,
         "payee": r.vendor_name, "amount": str(r.payment_amount), "currency": r.currency}
        for r in pas
    ]
    rows += [
        {"doc_kind": "expense_claim", "doc_id": str(c.id), "doc_number": c.claim_number,
         "payee": c.employee_name, "amount": str(c.total_amount), "currency": c.currency}
        for c in claims
    ]
    return rows
```

Replace `create_batch`:

```python
async def create_batch(db: AsyncSession, *, docs: list[tuple[str, uuid.UUID]],
                       payment_method: str, batch_date: date,
                       created_by: uuid.UUID) -> PaymentBatch:
    """Snapshot selected approved PAs and/or claims into a draft batch. Raises
    ValueError on empty / not-found / non-approved / mixed-currency selection."""
    if not docs:
        raise ValueError("Select at least one document to pay")
    pa_ids = [d_id for kind, d_id in docs if kind in ("pa", "pa_dir")]
    claim_ids = [d_id for kind, d_id in docs if kind == "expense_claim"]

    pas = (await db.execute(
        select(PaymentApplication).where(PaymentApplication.id.in_(pa_ids))
    )).scalars().all() if pa_ids else []
    claims = (await db.execute(
        select(ExpenseClaim).where(ExpenseClaim.id.in_(claim_ids))
    )).scalars().all() if claim_ids else []

    found_pa = {p.id for p in pas}
    found_claim = {c.id for c in claims}
    missing = ([str(d) for d in pa_ids if d not in found_pa]
               + [str(d) for d in claim_ids if d not in found_claim])
    if missing:
        raise ValueError(f"Documents not found: {missing}")
    not_approved = ([p.pa_number for p in pas if p.status != "approved"]
                    + [c.claim_number for c in claims if c.status != "approved"])
    if not_approved:
        raise ValueError(f"Not in approved status: {not_approved}")
    currencies = {p.currency for p in pas} | {c.currency for c in claims}
    if len(currencies) > 1:
        raise ValueError(f"A batch must be single-currency; got {sorted(currencies)}")

    total = (sum((p.payment_amount for p in pas), Decimal("0"))
             + sum((c.total_amount for c in claims), Decimal("0")))
    batch = PaymentBatch(
        batch_number=await _next_batch_number(db),
        batch_date=batch_date, status=DRAFT,
        currency=next(iter(currencies)), total=total,
        payment_method=payment_method, created_by=created_by,
    )
    db.add(batch)
    await db.flush()
    for p in pas:
        db.add(PaymentBatchLine(
            batch_id=batch.id,
            doc_kind="pa_dir" if p.po_id is None else "pa",
            doc_id=p.id, doc_number=p.pa_number, amount=p.payment_amount,
        ))
    for c in claims:
        db.add(PaymentBatchLine(
            batch_id=batch.id, doc_kind="expense_claim",
            doc_id=c.id, doc_number=c.claim_number, amount=c.total_amount,
        ))
    await db.flush()
    return batch
```

(`execute_batch` is unchanged.)

- [ ] **Step 5: Switch the create-batch request schema + endpoint**

In `uniops/finance-api/app/api/v1/payments.py`, replace `CreateBatchRequest` and add `DocRef`:

```python
class DocRef(BaseModel):
    doc_kind: str
    doc_id: uuid.UUID


class CreateBatchRequest(BaseModel):
    docs: list[DocRef]
    payment_method: str = "bank_transfer"
    batch_date: date | None = None
```

In the `create_batch` endpoint, change the crud call's first kwarg:

```python
        batch = await batch_crud.create_batch(
            db, docs=[(d.doc_kind, d.doc_id) for d in body.docs],
            payment_method=body.payment_method,
            batch_date=body.batch_date or date.today(),
            created_by=uuid.UUID(user["sub"]),
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_payment_batch.py -v` → all PASS (incl. the 2 new claim tests).
Then run the full finance-api suite: `pytest -q` → no regressions.

- [ ] **Step 7: (No commit — leave changes in the working tree.)**

---

### Task 2: Portal — Payment Batch page shows + submits claims

**Files:**
- Modify: `uniops/portal/src/pages/finance/PaymentBatchPage.tsx`

**Interfaces:**
- Consumes: `/payments/due` rows now carry `payee` + `expense_claim`; `POST /payments/batches` expects `{ docs: [{doc_kind, doc_id}] }` (Task 1).

- [ ] **Step 1: Rename the due field and update the due table**

In `uniops/portal/src/pages/finance/PaymentBatchPage.tsx`, change the `Due` interface field `vendor_name` → `payee`:

```tsx
interface Due {
  doc_kind: string; doc_id: string; doc_number: string | null
  payee: string; amount: string; currency: string
}
```

In the due table, change the "Vendor" header to "Payee", render `d.payee`, and make the Type cell handle claims:

```tsx
                <th className="px-3 py-2">Payee</th>
```

```tsx
                  <td className="px-3 py-2">{d.payee}</td>
                  <td className="px-3 py-2 text-xs text-neutral-500">
                    {d.doc_kind === 'expense_claim' ? 'Claim' : d.doc_kind === 'pa_dir' ? 'Direct PA' : 'PA'}
                  </td>
```

- [ ] **Step 2: Send `docs` refs when creating a batch**

Change the `createBatch` mutation's `mutationFn`:

```tsx
  const createBatch = useMutation({
    mutationFn: () => financeApi.post<Batch>('/payments/batches', {
      docs: [...selected].map((id) => {
        const d = due.find((x) => x.doc_id === id)!
        return { doc_kind: d.doc_kind, doc_id: id }
      }),
      payment_method: 'bank_transfer',
    }),
    onSuccess: (b) => {
      flash('ok', `Batch ${b.batch_number} created (${selected.size} items)`)
      setSelected(new Set())
      qc.invalidateQueries({ queryKey: ['payment-batches'] })
      qc.invalidateQueries({ queryKey: ['payments-due'] })
      setOpenBatch(b.id)
    },
    onError: (e: Error) => flash('err', e.message),
  })
```

- [ ] **Step 3: Show a Type column in the batch detail lines**

In `BatchDetailModal`, add a "Type" header to the lines table and a matching cell. Change the lines `<thead>` row to:

```tsx
                  <tr>
                    <th className="px-3 py-2">Document</th>
                    <th className="px-3 py-2 w-24">Type</th>
                    <th className="px-3 py-2 w-24">Status</th>
                    <th className="px-3 py-2 w-32 text-right">Amount</th>
                  </tr>
```

and the line row to:

```tsx
                    <tr key={ln.id} className="border-t border-neutral-100">
                      <td className="px-3 py-2">
                        <span className="font-mono text-xs">{ln.doc_number || ln.doc_id.slice(0, 8)}</span>
                        {ln.error && <div className="text-xs text-red-600">{ln.error}</div>}
                      </td>
                      <td className="px-3 py-2 text-xs text-neutral-500">
                        {ln.doc_kind === 'expense_claim' ? 'Claim' : ln.doc_kind === 'pa_dir' ? 'Direct PA' : 'PA'}
                      </td>
                      <td className="px-3 py-2"><StatusPill status={ln.status} /></td>
                      <td className="px-3 py-2 text-right font-mono">{fmtMoney(ln.amount)}</td>
                    </tr>
```

- [ ] **Step 4: Typecheck**

Run (in `uniops/portal/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: PASS, no errors (no remaining `vendor_name` references; `docs` payload typed).

- [ ] **Step 5: Manual verification**

With an approved PA and an approved Claim of the same currency: both appear in "Due for payment" (the claim under "Payee" with Type "Claim"); selecting both and Create Batch → Open → pick a bank → Execute marks both lines paid; afterward the PA shows `processed` and the Claim shows `paid`.

---

## Notes

- No commit steps: leave changes in the working tree for the batch commit.
- Task 1 (backend) must land before Task 2's manual verification, but the two files don't overlap; Task 2's typecheck does not depend on Task 1.
- The executor, `execute_batch`, the batch model, and the auto status-flip are intentionally untouched — they already handle `expense_claim`.
