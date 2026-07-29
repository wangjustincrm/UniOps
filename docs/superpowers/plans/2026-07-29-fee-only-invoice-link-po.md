# Fee-only Invoice → Link to PO Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a fee-only vendor invoice (every line marked non-PO fee, e.g. a standalone freight invoice) be confirmed and associated with a same-vendor PO for traceability, while the fees are paid in full via the AP header.

**Architecture:** Backend `match()` gains a "no allocations" branch: instead of rejecting, it requires a `reference_po_id`, stamps it onto the invoice header (`po_id`/`po_number`), zeroes variance, and sets status `matched`. Frontend adds a mandatory "Link this invoice to a PO" dropdown (sourced strictly from the existing same-vendor candidate list) shown only when no line is allocated.

**Tech Stack:** FastAPI + SQLAlchemy async (epms-api, Python 3.12, pytest); React + TypeScript + TanStack Query (epms).

## Global Constraints

- Branch `feature/fee-only-invoice-link-po`, worktree `c:/Project/uniops-feeonly`, off `main` 1f2eb99. Never commit to `main`. ([[feedback_uniops_multisession_release_discipline]])
- **Zero DB migrations.**
- Semantics are **reference-only**: linking a PO must NOT create an allocation, NOT change the PO's invoiced total or variance.
- `reference_po_id` takes effect **only when there are zero allocations**; mixed/allocated invoices are unchanged.
- The "Link to PO" dropdown must source **only** from the existing `pos` prop (same-vendor `match-candidates` list, `epms-api/app/api/v1/invoices.py:352`). Never query all POs.
- Frontend user-facing copy in **English** ([[feedback_uniops_ui_english_only]]). tsc baseline is **59** and must not increase ([[project_uniops_pa_chain_attachments]]): gate is `tsc -p tsconfig.app.json --noEmit`.
- Do **not** push, build images, or deploy without explicit user approval.
- Test env per [[feedback_uniops_admin_test_db_env]]: override `POSTGRES_*` to local docker `uniops_postgres`, DB `epms_test`, pass `JWT_SECRET_KEY`; run serialized ([[feedback_uniops_test_db_concurrency]]).

---

### Task 1: Backend — fee-only match branch (schema + crud + endpoint)

**Files:**
- Modify: `epms-api/app/schemas/invoice.py` (`InvoiceMatchRequest`, ~line 83-92)
- Modify: `epms-api/app/crud/invoice.py` (`match()`, remove gate ~line 277-278; rollup ~line 391-427; add exception class near `AllocationImbalance` ~line 231)
- Modify: `epms-api/app/api/v1/invoices.py` (`match_invoice` import ~line 265 + except tuple ~line 324)
- Test: `epms-api/tests/test_invoice_allocations.py` (append)

**Interfaces:**
- Consumes: existing `InvoiceMatchRequest`, `match(db, invoice, req, matched_by, require_review)`, `AllocationImbalance`, `finance_sync.sync_ap_invoice` (unchanged).
- Produces:
  - `InvoiceMatchRequest.reference_po_id: uuid.UUID | None`
  - `FeeOnlyLinkRequired(ValueError)` in `app/crud/invoice.py`
  - `match()` accepts `allocations=[]` + `non_po_lines` covering full amount + `reference_po_id` → returns invoice with `status="matched"`, `po_id=reference_po`, `variance=0`, zero allocation rows.

- [ ] **Step 1: Write the failing tests**

Append to `epms-api/tests/test_invoice_allocations.py`:

```python
@pytest.mark.asyncio
async def test_fee_only_invoice_links_to_po(admin_client):
    """Freight-only invoice: single line marked non-PO fee, linked to a PO by
    reference_po_id → matched, header carries the PO, no allocations, zero variance."""
    v = await _make_vendor(admin_client, "VND-FEEONLY-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "Goods", "qty": "1", "unit": "EA", "unit_price": "1915.90"}])

    inv = await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="132.52", tax_amount="17.23",
        line_items=[{"description": "FREIGHT CHARGES", "quantity": "1",
                     "unit_price": "132.52", "line_total": "132.52"}]))
    inv = inv.json()
    fee_line = inv["line_items"][0]["id"]

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [],
        "non_po_lines": [{"line_id": fee_line, "note": "freight"}],
        "reference_po_id": po["id"],
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "matched"
    assert data["po_id"] == po["id"]
    assert data["po_number"] == po["number"]
    assert data["allocations"] == []
    assert float(data["variance"]) == 0.0
    assert float(data["po_total"]) == 0.0
    fee = next(li for li in data["line_items"] if li["id"] == fee_line)
    assert fee["non_po_fee"] is True


@pytest.mark.asyncio
async def test_fee_only_invoice_without_link_rejected(admin_client):
    """Fee-only invoice with no reference_po_id → 422, cannot confirm."""
    v = await _make_vendor(admin_client, "VND-FEEONLY-02")
    inv = await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="132.52", tax_amount="0.00",
        line_items=[{"description": "FREIGHT", "quantity": "1",
                     "unit_price": "132.52", "line_total": "132.52"}]))
    inv = inv.json()
    fee_line = inv["line_items"][0]["id"]

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [],
        "non_po_lines": [{"line_id": fee_line, "note": "freight"}],
    })
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_fee_only_invoice_imbalanced_rejected(admin_client):
    """Zero allocations but fees don't cover the full pre-tax amount → 422."""
    v = await _make_vendor(admin_client, "VND-FEEONLY-03")
    po = await _make_issued_po(admin_client, v["id"])
    inv = await admin_client.post(INV_URL, json=_inv_payload(
        v["id"], amount="1000.00", tax_amount="0.00",
        line_items=[
            {"description": "part-a", "quantity": "1", "unit_price": "600.00", "line_total": "600.00"},
            {"description": "part-b", "quantity": "1", "unit_price": "400.00", "line_total": "400.00"},
        ]))
    inv = inv.json()
    only_line = inv["line_items"][0]["id"]   # mark only 600 of 1000 as fee

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [],
        "non_po_lines": [{"line_id": only_line, "note": "partial fee"}],
        "reference_po_id": po["id"],
    })
    assert r.status_code == 422, r.text


def test_match_request_accepts_reference_po_id():
    from app.schemas.invoice import InvoiceMatchRequest
    import uuid
    req = InvoiceMatchRequest(allocations=[], reference_po_id=uuid.uuid4())
    assert req.reference_po_id is not None
    # optional by default
    assert InvoiceMatchRequest(allocations=[]).reference_po_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `epms-api`, test env per Global Constraints):
```bash
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=epms_test \
POSTGRES_USER=uniops POSTGRES_PASSWORD=uniops JWT_SECRET_KEY=test-secret \
python -m pytest tests/test_invoice_allocations.py -k "fee_only or reference_po_id" -v
```
Expected: `test_match_request_accepts_reference_po_id` FAILS (unexpected keyword `reference_po_id`); `test_fee_only_invoice_links_to_po` FAILS (404/500 — gate raises "At least one allocation is required"); imbalanced test may already 422 but keep it.

- [ ] **Step 3: Add `reference_po_id` to the schema**

In `epms-api/app/schemas/invoice.py`, inside `class InvoiceMatchRequest`, after `non_po_lines` (~line 87) add:
```python
    # When there are NO PO allocations (fee-only invoice, e.g. standalone freight),
    # the PO this invoice is associated with for traceability. Ignored when
    # allocations are present. The fees are paid in full via the AP header.
    reference_po_id: uuid.UUID | None = None
```

- [ ] **Step 4: Add the `FeeOnlyLinkRequired` exception**

In `epms-api/app/crud/invoice.py`, next to `class AllocationImbalance` (~line 231):
```python
class FeeOnlyLinkRequired(ValueError):
    """Raised when a fee-only invoice (no PO allocations) is confirmed without a
    reference PO to link it to (→ HTTP 422)."""
```

- [ ] **Step 5: Remove the hard gate and branch the header rollup**

In `epms-api/app/crud/invoice.py` `match()`:

(a) Delete the early gate (currently ~line 277-278):
```python
    allocs = await _normalize_allocations(invoice, req)
    if not allocs:
        raise ValueError("At least one allocation is required")
```
→ becomes:
```python
    allocs = await _normalize_allocations(invoice, req)
```
(The balance check further down still enforces that a fee-only invoice's non-PO fees cover the full pre-tax amount; an empty allocation list is now allowed.)

(b) Replace the rollup block (currently ~line 391-427, from `# 5. roll up...` down to the `invoice.matched_reference_total = None` line) with:
```python
    # 5. roll up to invoice header (summary + backward-compat single values)
    invoice.matched_at = now
    invoice.matched_by = matched_by
    invoice.matched_by_name = (await db.execute(
        select(User.full_name).where(User.id == matched_by)
    )).scalar_one_or_none()

    if allocs:
        first = allocs[0]
        primary_po = po_cache[first.po_id]
        invoice.po_id = primary_po.id
        invoice.po_number = primary_po.number

        seen: set[tuple] = set()
        summary_reference = Decimal("0")
        for row in new_rows:
            key = (row.po_id, row.po_line_id)
            if key in seen:
                continue
            seen.add(key)
            if row.po_line_id is not None:
                summary_reference += (await db.execute(
                    select(PoLineItem.line_total).where(PoLineItem.id == row.po_line_id)
                )).scalar_one()
            else:
                summary_reference += po_cache[row.po_id].subtotal
        invoice.po_total = summary_reference
        # Header variance measures only the PO-matched portion of the invoice: a
        # non-PO fee line's pre-tax amount is not part of the PO reference, so it
        # must be subtracted from the invoice side here or a perfectly-balanced
        # mixed invoice (PO alloc + non-PO fee) would show a spurious variance.
        invoice.variance = (invoice.amount - excluded_total) - summary_reference
        invoice.variance_pct = (
            (invoice.variance / summary_reference * 100).quantize(Decimal("0.0001"))
            if summary_reference != Decimal("0") else Decimal("0")
        )
    else:
        # Fee-only invoice: no PO allocations. Link it to a reference PO for
        # traceability; the fees are paid in full via the AP header. There is no
        # PO line reference to measure variance against, so variance is zero.
        if req.reference_po_id is None:
            raise FeeOnlyLinkRequired("Link a PO to confirm a fee-only invoice")
        ref_po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == req.reference_po_id)
        )).scalar_one_or_none()
        if ref_po is None:
            raise ValueError(f"Purchase order {req.reference_po_id} not found")
        invoice.po_id = ref_po.id
        invoice.po_number = ref_po.number
        invoice.po_total = Decimal("0")
        invoice.variance = Decimal("0")
        invoice.variance_pct = Decimal("0")

    invoice.matched_po_line_ids = None
    invoice.matched_reference_total = None
```
Leave the GR-handling block and the status block (`all_zero = ...`) exactly as-is: for a fee-only invoice `new_rows` is empty, so `all_zero` is `True` (review skipped) and `any_exception` is `False` → `status = "matched"`, `variance == 0` so no exception_reason is set.

- [ ] **Step 6: Wire `FeeOnlyLinkRequired` to a 422 in the endpoint**

In `epms-api/app/api/v1/invoices.py`, the import inside `match_invoice` (~line 265):
```python
    from app.crud.invoice import AllocationImbalance, LegacyMatchUnsupported, FeeOnlyLinkRequired
```
and the except tuple (~line 324):
```python
    except (AllocationImbalance, LegacyMatchUnsupported, FeeOnlyLinkRequired) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
```

- [ ] **Step 7: Run tests to verify they pass**

Run the same command as Step 2. Expected: all four new tests PASS.

- [ ] **Step 8: Run the full allocation suite for regressions**

```bash
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=epms_test \
POSTGRES_USER=uniops POSTGRES_PASSWORD=uniops JWT_SECRET_KEY=test-secret \
python -m pytest tests/test_invoice_allocations.py -v
```
Expected: previously-green tests still pass (baseline was 18/18 for this suite); new tests green. If any pre-existing test 502s, confirm it is the known environment gap ([[project_uniops_invoice_non_po_fee_lines]]), not a regression from this change.

- [ ] **Step 9: Commit**

```bash
git add epms-api/app/schemas/invoice.py epms-api/app/crud/invoice.py epms-api/app/api/v1/invoices.py epms-api/tests/test_invoice_allocations.py
git commit -m "feat(invoice): allow fee-only invoice to link to a PO (reference only)"
```

---

### Task 2: Frontend — "Link to PO" dropdown + payload wiring

**Files:**
- Modify: `epms/src/services/invoices.ts` (`MatchInvoiceBody`, ~line 120-127)
- Modify: `epms/src/pages/invoices/InvoiceAllocationPanel.tsx` (Props `onSubmit`, panel body, submit)
- Modify: `epms/src/pages/invoices/MatchPanel.tsx` (`handleMatch`, ~line 35-45)
- Modify: `epms/src/pages/invoices/InvoiceListPage.tsx` (`handleAllocSubmit`, ~line 326-331)

**Interfaces:**
- Consumes: `ApiPo` (`.id`, `.number`), `AllocationInput`, `NonPoLineInput`, `useMatchInvoice` (spreads `...body`), backend `reference_po_id`.
- Produces:
  - `MatchInvoiceBody.reference_po_id?: string`
  - `InvoiceAllocationPanel` `onSubmit` payload shape `{ allocations, nonPoLines, referencePoId }` (`referencePoId: string | null`).

- [ ] **Step 1: Add `reference_po_id` to the service contract**

In `epms/src/services/invoices.ts`, `interface MatchInvoiceBody` (~line 120), add:
```typescript
  reference_po_id?: string
```

- [ ] **Step 2: Add the dropdown, state, and gating to the panel**

In `epms/src/pages/invoices/InvoiceAllocationPanel.tsx`:

(a) Change the `onSubmit` prop type (Props interface, ~line 14):
```typescript
  onSubmit: (payload: { allocations: AllocationInput[]; nonPoLines: NonPoLineInput[]; referencePoId: string | null }) => void
```

(b) Add reference-PO state next to the other `useState` hooks (~line 22):
```typescript
  const [referencePoId, setReferencePoId] = useState<string>('')
```

(c) Derive whether any line is allocated, near `unallocated`/`balanced` (~line 78):
```typescript
  const hasAllocations = Object.keys(assign).length > 0
  // Fee-only invoice (nothing allocated to a PO): must link a PO to confirm.
  const needsReferencePo = balanced && !hasAllocations
  const canSubmit = balanced && (hasAllocations || !!referencePoId)
```

(d) Update `submit` (~line 99) to include the reference PO (only meaningful when nothing is allocated):
```typescript
  const submit = () => onSubmit({
    allocations: buildAllocations(),
    nonPoLines: Object.entries(nonPo).map(([line_id, note]) => ({ line_id, note })),
    referencePoId: hasAllocations ? null : (referencePoId || null),
  })
```

(e) Render the dropdown just below the "Unallocated balance" header block, before the two-column grid (after the `excludedTotal` hint, ~line 123). It appears only when nothing is allocated. Options come strictly from the `pos` prop (already same-vendor candidates):
```tsx
      {!hasAllocations && (
        <div className="flex flex-col gap-1 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5">
          <label className="text-xs font-medium text-amber-800">Link this invoice to a PO</label>
          <span className="text-[11px] text-amber-700">
            This invoice has no PO line allocations. Link it to a purchase order (same vendor) for traceability — the charges are paid via the invoice header.
          </span>
          <select
            value={referencePoId}
            onChange={(e) => setReferencePoId(e.target.value)}
            className="mt-1 h-8 rounded-lg border border-neutral-300 bg-white px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value="">Select a purchase order…</option>
            {pos.map((po) => (
              <option key={po.id} value={po.id}>{po.number}</option>
            ))}
          </select>
        </div>
      )}
```

(f) Update the Confirm button (~line 224) to use `canSubmit` and hint when a PO link is missing:
```tsx
      <div className="flex flex-col items-end gap-1">
        {needsReferencePo && !referencePoId && (
          <span className="text-[11px] text-danger-600">Link a PO to confirm a fee-only invoice</span>
        )}
        <Button onClick={submit} disabled={!canSubmit || submitting}>
          {submitting ? 'Matching...' : 'Confirm allocation & match'}
        </Button>
      </div>
```
(Replace the existing `<div className="flex justify-end"> ... </div>` wrapper around the Button.)

- [ ] **Step 3: Thread `referencePoId` through `MatchPanel`**

In `epms/src/pages/invoices/MatchPanel.tsx`, `handleMatch` (~line 35):
```typescript
  const handleMatch = (payload: { allocations: AllocationInput[]; nonPoLines: NonPoLineInput[]; referencePoId: string | null }) => {
    matchInvoiceMutation.mutate(
      { id: inv.id, allocations: payload.allocations, non_po_lines: payload.nonPoLines,
        reference_po_id: payload.referencePoId ?? undefined },
      {
        onSuccess: () => {
          onClose()
          navigate(`/invoices/${inv.id}`)
        },
      }
    )
  }
```

- [ ] **Step 4: Thread `referencePoId` through `InvoiceListPage`**

In `epms/src/pages/invoices/InvoiceListPage.tsx`, `handleAllocSubmit` (~line 326):
```typescript
    const handleAllocSubmit = (payload: { allocations: AllocationInput[]; nonPoLines: NonPoLineInput[]; referencePoId: string | null }) => {
      matchInvoiceMutation.mutate(
        { id: createdInv.id, allocations: payload.allocations, non_po_lines: payload.nonPoLines,
          reference_po_id: payload.referencePoId ?? undefined, gr_id: linkedGr?.id },
```
(Keep the rest of the mutate call — `onSuccess` handlers etc. — unchanged.)

- [ ] **Step 5: Type-check (must not exceed baseline 59)**

Run (from `epms`):
```bash
npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS" || true
```
Expected: `59` (unchanged). If higher, fix the introduced errors before committing. Confirm no new error references the four modified files.

- [ ] **Step 6: Commit**

```bash
git add epms/src/services/invoices.ts epms/src/pages/invoices/InvoiceAllocationPanel.tsx epms/src/pages/invoices/MatchPanel.tsx epms/src/pages/invoices/InvoiceListPage.tsx
git commit -m "feat(invoice): fee-only invoice Link-to-PO dropdown in allocation panel"
```

---

### Task 3: Manual smoke + spec/memory sync

**Files:** none (verification + docs)

- [ ] **Step 1: Backend smoke via the dev stack** (串行联调, [[feedback_uniops_dev_containers_mount_main_checkout]]: the dev stack mounts the main checkout — to exercise this branch, check it out in the integration checkout `c:/Project/uniops` or rebuild the dev epms-api against this worktree). Reproduce the reported case: upload/select a freight-only invoice, mark the single line "Mark as other fee", pick a PO in "Link this invoice to a PO", Confirm. Verify: invoice → `matched`, PO shows on the invoice header, and the invoice appears under that PO's related invoices (filter is `or_(Invoice.po_id == po_id, by-allocation)`).

- [ ] **Step 2: Negative smoke** — with all lines marked fee and **no** PO selected, confirm the Confirm button is disabled with the "Link a PO to confirm a fee-only invoice" hint; with a partial fee (unbalanced), confirm the balance stays red and Confirm disabled.

- [ ] **Step 3: Update the feature memory** — append to `project_uniops_invoice_non_po_fee_lines` (or a new memory) that fee-only invoices can now be confirmed by linking a reference PO; branch `feature/fee-only-invoice-link-po`, zero migrations, not yet merged/deployed. Note it does NOT change mixed-invoice behavior and does not touch the mixed-invoice pay-through gap.

- [ ] **Step 4: Report to the user** — summarize what changed, that it is committed on the feature branch only, and that release (merge to main + image build) awaits their go per [[feedback_uniops_multisession_release_discipline]] R4/R5.

---

## Self-Review Notes

- **Spec coverage:** schema (T1.S3), fee-only branch + variance zero + status matched (T1.S5), 422 wiring (T1.S4/S6), finance_sync unchanged (no task — verified sufficient in spec), dropdown same-vendor-only (T2.S2e sources `pos`), mandatory link gating (T2.S2c/f), payload threading (T2.S1/S3/S4), visibility under PO (T3.S1), tests (T1.S1). All covered.
- **Mixed invoices unchanged:** `reference_po_id` only read in the `else` (no-allocations) branch; the `if allocs` branch is byte-for-byte the original rollup.
- **Type consistency:** payload shape `{ allocations, nonPoLines, referencePoId }` is identical across `InvoiceAllocationPanel` Props, `MatchPanel.handleMatch`, and `InvoiceListPage.handleAllocSubmit`; `reference_po_id?: string` on `MatchInvoiceBody`; `FeeOnlyLinkRequired` defined in crud and imported in the endpoint.
