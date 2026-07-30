# Invoice Match-PO Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three coordinated invoice→PO match improvements: (#4) let the uploader match their own invoice without an AP-assigned task; (#3) auto-match on upload when a PO is known; (#2) a "By total amount" match mode for invoices whose lines carry no unit price.

**Architecture:** #4 widens two backend auth gates and one frontend visibility helper. #3 auto-runs the existing match on upload (line-level if it balances, else total-value legacy path), no panel. #2 adds a mode toggle on the allocation panel plus a new total-value view that drags the whole invoice onto PO header cards, submitting header-level allocations; the only backend addition is a per-PO `already_allocated_total` on match-candidates.

**Tech Stack:** FastAPI + SQLAlchemy async (epms-api, Python 3.12, pytest); React + TypeScript + TanStack Query (epms).

## Global Constraints

- Branch `feature/invoice-match-po-improvements`, worktree `c:/Project/uniops-matchpo`, off `main` 6d3a9dd. Never commit to `main`. ([[feedback_uniops_multisession_release_discipline]])
- **Zero DB migrations.**
- Frontend user-facing copy **English** ([[feedback_uniops_ui_english_only]]).
- Frontend **tsc baseline 59** must not increase; gate from `epms`: `npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"` → `59`. No `--ignoreDeprecations` (epms is TS 5.9.3).
- Decimals serialize to JSON **strings** — new numeric response fields arrive as strings; `Number()` them before math ([[feedback_uniops_decimal_as_string]]).
- Backend tests: run from `c:/Project/uniops-matchpo/epms-api` with `POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=epms POSTGRES_USER=epms POSTGRES_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 JWT_SECRET_KEY=test-secret python -m pytest tests/test_invoice_allocations.py -v`. Run this suite alone ([[feedback_uniops_test_db_concurrency]]).
- Reference-only semantics of the existing fee-only path must remain intact; do not alter `match()`'s `if allocs / else` branches.
- Do **not** push / build images / deploy without explicit user approval.

## Implementation order
Task 1 (#4 backend) → Task 2 (#4 frontend) → Task 3 (#2 backend field) → Task 4 (#3 auto-match) → Task 5 (#2 total mode UI). Tasks 1-4 are small/independent; Task 5 is the large one and depends only on Task 3.

---

### Task 1: #4 backend — uploader may match their own invoice

**Files:**
- Modify: `epms-api/app/api/v1/invoices.py` (`match_invoice` ~248-268, `list_match_candidates` ~346-358)
- Test: `epms-api/tests/test_invoice_allocations.py` (append)

**Interfaces:**
- Produces: `match_invoice`/`list_match_candidates` accept a caller who is `invoice.uploaded_by`; a self-match sets `require_review = not (is_ap or is_uploader)`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_invoice_allocations.py`. These use the `requester_client` fixture (a non-AP user) and set `uploaded_by` to that user by creating the invoice through a direct session with the client's JWT subject:

```python
import base64, json

def _jwt_sub(client) -> str:
    tok = client.headers["Authorization"].split(" ", 1)[1]
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["sub"]


async def _create_invoice_uploaded_by(admin_client, vendor_id, uploader_id, **overrides):
    """Create an invoice row owned by `uploader_id` (bypasses the upload
    permission gate — we are testing MATCH auth, not upload auth)."""
    import uuid as _uuid
    import app.db.session as session_module
    from app.crud import invoice as invoice_crud
    from app.schemas.invoice import InvoiceCreate
    payload = _inv_payload(vendor_id, **overrides)
    async with session_module.AsyncSessionLocal() as db:
        inv = await invoice_crud.create(
            db, InvoiceCreate(**{**payload, "vendor_id": _uuid.UUID(vendor_id)}),
            vendor_name="Alloc Vendor", uploaded_by=_uuid.UUID(uploader_id))
        await db.commit()
        return {"id": str(inv.id), "line_items": [{"id": str(li.get("id"))} for li in (inv.line_items or [])]}


@pytest.mark.asyncio
async def test_uploader_can_match_own_invoice(admin_client, requester_client):
    """A non-AP uploader can match their own invoice directly (no AP task)."""
    v = await _make_vendor(admin_client, "VND-SELFMATCH-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "W", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    uploader_id = _jwt_sub(requester_client)
    inv = await _create_invoice_uploaded_by(admin_client, v["id"], uploader_id,
        amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"}])
    # requester (uploader, non-AP) matches via the whole-invoice legacy path
    r = await requester_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": po["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched"          # exact vs subtotal, no match_review


@pytest.mark.asyncio
async def test_non_uploader_non_ap_cannot_match(admin_client, requester_client):
    """A non-AP user who did NOT upload the invoice is still blocked (403)."""
    v = await _make_vendor(admin_client, "VND-SELFMATCH-02")
    po = await _make_issued_po(admin_client, v["id"])
    other_id = "00000000-0000-0000-0000-000000000123"   # not the requester
    inv = await _create_invoice_uploaded_by(admin_client, v["id"], other_id,
        amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"}])
    r = await requester_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": po["id"]})
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_uploader_can_list_match_candidates(admin_client, requester_client):
    v = await _make_vendor(admin_client, "VND-SELFMATCH-03")
    await _make_issued_po(admin_client, v["id"])
    uploader_id = _jwt_sub(requester_client)
    inv = await _create_invoice_uploaded_by(admin_client, v["id"], uploader_id,
        amount="1000.00", tax_amount="0.00",
        line_items=[{"description": "x", "quantity": "1", "unit_price": "1000.00", "line_total": "1000.00"}])
    r = await requester_client.get(f"{INV_URL}/{inv['id']}/match-candidates")
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run the suite (Global Constraints command) filtered `-k "uploader or non_uploader"`. Expected: the two positive tests FAIL with 403 (uploader currently not allowed); the negative test may already pass.

- [ ] **Step 3: Widen `match_invoice` auth + review flag**

In `epms-api/app/api/v1/invoices.py` `match_invoice`, reorder so `inv` is fetched before the auth decision, then include the uploader:

```python
    caller_id = uuid.UUID(user["sub"])
    is_ap = user.get("role") in _AP_ROLES
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    is_uploader = inv.uploaded_by == caller_id
    if not is_ap and not is_uploader and not await _has_open_match_task(db, caller_id, invoice_id):
        raise HTTPException(status_code=403, detail="Not allowed to match this invoice")
    if inv.status not in ("unmatched", "exception"):
        raise HTTPException(status_code=409, detail=f"Invoice already in status '{inv.status}'")
    require_review = not (is_ap or is_uploader)
```
(Delete the old lines 257-264 that they replace: the pre-fetch auth check, the separate `inv` fetch/None check, the status check, and the old `require_review = not is_ap`.)

- [ ] **Step 4: Widen `list_match_candidates` auth**

In `list_match_candidates` (~356-358), after `inv` is fetched:
```python
    caller_id = uuid.UUID(user["sub"])
    is_uploader = inv.uploaded_by == caller_id
    if user.get("role") not in _AP_ROLES and not is_uploader and not await _has_open_match_task(db, caller_id, invoice_id):
        raise HTTPException(status_code=403, detail="Not allowed to match this invoice")
```

- [ ] **Step 5: Run tests to verify they pass**

Run `-k "uploader or non_uploader"`. Expected: all three PASS.

- [ ] **Step 6: Full-suite regression + commit**

Run the full `test_invoice_allocations.py`. Expected: all green (prior fee-only tests + new). Then:
```bash
git add epms-api/app/api/v1/invoices.py epms-api/tests/test_invoice_allocations.py
git commit -m "feat(invoice): uploader may match their own invoice without an AP task"
```

---

### Task 2: #4 frontend — match visibility for the uploader

**Files:**
- Modify: `epms/src/pages/invoices/InvoiceListPage.tsx` (`canMatchInvoice` ~914)
- Modify: the invoice **detail** page match gate — find with `grep -rn "isAp\|MATCH_ROLES\|match_assignee_id" epms/src/pages/invoices/InvoiceDetailPage.tsx` (or wherever `MatchPanel` is mounted) and align it the same way.

**Interfaces:**
- Consumes: `ApiInvoice.uploaded_by` (already on the type, invoices.ts:61).
- Produces: `canMatchInvoice(inv)` returns true for the invoice's uploader.

- [ ] **Step 1: Extend `canMatchInvoice`**

In `InvoiceListPage.tsx` (~914):
```typescript
  const canMatchInvoice = (inv: ApiInvoice) =>
    isAp || inv.uploaded_by === user?.id ||
    (inv.match_assignee_id != null && inv.match_assignee_id === user?.id)
```

- [ ] **Step 2: Align the detail-page "Match to PO" gate**

In `epms/src/pages/invoices/InvoiceDetailPage.tsx`, the "Match to PO" button is gated at ~line 293:
```tsx
            (isAp || (inv.match_assignee_id != null && inv.match_assignee_id === user?.id)) && (
```
Add the uploader:
```tsx
            (isAp || inv.uploaded_by === user?.id || (inv.match_assignee_id != null && inv.match_assignee_id === user?.id)) && (
```
Do **not** change the `isAp && ...` gate at ~line 299 (that is an AP-only action — assign/resolve — not the match button).

- [ ] **Step 3: Type-check + commit**

```bash
npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"   # expect 59
git add epms/src/pages/invoices/InvoiceListPage.tsx epms/src/pages/invoices/InvoiceDetailPage.tsx
git commit -m "feat(invoice): show Match to PO to the invoice uploader"
```

---

### Task 3: #2 backend — per-PO `already_allocated_total` on match-candidates

**Files:**
- Modify: `epms-api/app/api/v1/invoices.py` (`list_match_candidates` ~369-386)
- Modify: `epms-api/app/schemas/po.py` (`PoResponse`)
- Test: `epms-api/tests/test_invoice_allocations.py` (append)

**Interfaces:**
- Produces: `PoResponse.already_allocated_total: Decimal | None` — Σ of every allocation on this PO from OTHER invoices (all `po_line_id`s), for the total-value mode's PO-remaining display.

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_match_candidates_report_already_allocated_total(admin_client):
    """A PO header-billed by one invoice reports already_allocated_total to the next."""
    v = await _make_vendor(admin_client, "VND-ALLOCTOT-01")
    po = await _make_issued_po(admin_client, v["id"],
        lines=[{"description": "W", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    # invoice 1 total-matches the whole PO (header-level, legacy path)
    inv1 = (await admin_client.post(INV_URL, json=_inv_payload(v["id"], amount="600.00", tax_amount="0.00",
        line_items=[{"description": "a", "quantity": "1", "unit_price": "600.00", "line_total": "600.00"}]))).json()
    r1 = await admin_client.post(f"{INV_URL}/{inv1['id']}/match", json={"po_id": po["id"]})
    assert r1.status_code == 200, r1.text
    # invoice 2 asks for candidates → sees 600 already allocated on that PO
    inv2 = (await admin_client.post(INV_URL, json=_inv_payload(v["id"], amount="400.00", tax_amount="0.00",
        line_items=[{"description": "b", "quantity": "1", "unit_price": "400.00", "line_total": "400.00"}]))).json()
    cand = (await admin_client.get(f"{INV_URL}/{inv2['id']}/match-candidates")).json()
    the_po = next(p for p in cand["items"] if p["id"] == po["id"])
    assert float(the_po["already_allocated_total"]) == 600.0
    # and it EXCLUDES the requesting invoice's own allocations (0 here)
    cand_self = (await admin_client.get(f"{INV_URL}/{inv1['id']}/match-candidates")).json()
    po_self = next(p for p in cand_self["items"] if p["id"] == po["id"])
    assert float(po_self["already_allocated_total"] or 0) == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run `-k already_allocated_total`. Expected: KeyError/None on `already_allocated_total` (field absent).

- [ ] **Step 3: Add the field to `PoResponse`**

In `epms-api/app/schemas/po.py`, add to `PoResponse` (near the other computed display field; PoResponse uses `from_attributes`):
```python
    already_allocated_total: Decimal | None = None
```

- [ ] **Step 4: Compute and attach it in `list_match_candidates`**

In `list_match_candidates`, inside the `if pos:` block (alongside the existing per-line `sums`), add a per-PO aggregate over ALL po_line_ids from other invoices, and attach it before `model_validate`:
```python
        po_totals = dict((await db.execute(
            select(InvoicePoAllocation.po_id,
                   sa_func.sum(InvoicePoAllocation.allocated_amount))
            .where(InvoicePoAllocation.po_id.in_([p.id for p in pos]),
                   InvoicePoAllocation.invoice_id != invoice_id)
            .group_by(InvoicePoAllocation.po_id)
        )).all())
        for po in pos:
            for li in po.line_items:
                li.already_allocated = sums.get(li.id)
            po.already_allocated_total = po_totals.get(po.id) or Decimal("0")
```
(`Decimal` is already imported in schemas; in the endpoint import it if needed: `from decimal import Decimal`.)

- [ ] **Step 5: Run to verify pass, full-suite regression, commit**

Run `-k already_allocated_total` (pass), then the full suite (green). Then:
```bash
git add epms-api/app/api/v1/invoices.py epms-api/app/schemas/po.py epms-api/tests/test_invoice_allocations.py
git commit -m "feat(invoice): match-candidates report per-PO already_allocated_total"
```

---

### Task 4: #3 frontend — auto-match on upload when a PO is known

**Files:**
- Modify: `epms/src/pages/invoices/InvoiceListPage.tsx` (`UploadModal`: `handleSubmit` ~274-286, and the prefill heuristic ~304-324 which must be extracted for reuse)

**Interfaces:**
- Consumes: `matchInvoiceMutation` (`useMatchInvoice`), `MatchInvoiceBody` (`po_id?`, `allocations?`, `gr_id?`), the prefill amount-pairing heuristic.
- Produces: on upload with a recognized PO, the invoice is matched server-side with no panel.

- [ ] **Step 1: Extract the line-level pairing into a reusable helper**

Inside `UploadModal`, above `handleSubmit`, add a pure helper that mirrors the existing prefill logic and returns balanced line-level allocations or `null`:
```typescript
  // Greedily pair each invoice line with an unused PO line of equal pre-tax amount.
  // Returns line-level allocations ONLY if every invoice line is paired AND they
  // sum to the invoice pre-tax total (a clean line-level match); else null.
  const buildLineLevelAllocations = (inv: ApiInvoice, po: ApiPo): AllocationInput[] | null => {
    const invLines = inv.line_items ?? []
    if (invLines.length === 0) return null
    const used = new Set<string>()
    const allocs: AllocationInput[] = []
    for (const l of invLines) {
      if (!l.id) return null
      const amt = Number(l.line_total)
      const target = po.line_items.find((pl) => !used.has(pl.id) && Math.abs(Number(pl.line_total) - amt) < 0.01)
      if (!target) return null
      used.add(target.id)
      allocs.push({ invoice_line_id: l.id, po_id: po.id, po_line_id: target.id, allocated_amount: amt, allocated_tax: 0 })
    }
    const sum = allocs.reduce((s, a) => s + a.allocated_amount, 0)
    if (Math.abs(sum - Number(inv.amount)) > 0.01) return null
    return allocs
  }
```

- [ ] **Step 2: Replace the "open panel" branch in `handleSubmit` with auto-match**

Replace the current block (~274-285):
```typescript
      const matcherRole = useAuthStore.getState().user?.role
      const canMatch = !!matcherRole && MATCH_ROLES.has(matcherRole)
      if (matchedPo && canMatch && (inv.line_items?.length ?? 0) > 0) {
        setCreatedInv(inv)
        return
      }
      onUploaded(inv.id)
```
with (the uploader can always match their own upload per Feature #4):
```typescript
      // A recognized PO → auto-match immediately (no second action). Line-level
      // when it balances cleanly, else whole-invoice total-value to that PO.
      // On any match error, fall back to the manual allocation panel.
      if (matchedPo) {
        const lineAllocs = buildLineLevelAllocations(inv, matchedPo)
        const linkedGr = grs.find((g) => g.po_id === matchedPo.id && g.status !== 'cancelled')
        matchInvoiceMutation.mutate(
          lineAllocs
            ? { id: inv.id, allocations: lineAllocs, gr_id: linkedGr?.id }
            : { id: inv.id, po_id: matchedPo.id, gr_id: linkedGr?.id },
          {
            onSuccess: () => onUploaded(inv.id),
            onError: () => setCreatedInv(inv),   // fall back to manual panel
          },
        )
        return
      }
      onUploaded(inv.id)
```
The `if (createdInv)` allocation-panel render block stays as the fallback path (reached only on match error now). Remove the now-unused local `canMatch`/`matcherRole` if the linter flags them; keep `MATCH_ROLES` import if still used elsewhere in the file.

- [ ] **Step 3: Type-check + commit**

```bash
npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"   # expect 59
git add epms/src/pages/invoices/InvoiceListPage.tsx
git commit -m "feat(invoice): auto-match on upload when a PO is recognized"
```

---

### Task 5: #2 frontend — "By total amount" match mode

**Files:**
- Create: `epms/src/pages/invoices/InvoiceTotalMatchPanel.tsx`
- Modify: `epms/src/pages/invoices/InvoiceAllocationPanel.tsx` (mode toggle + delegate; pre-tax headers on line view)
- Modify: `epms/src/services/po.ts` (`ApiPo.already_allocated_total`)

**Interfaces:**
- Consumes: `ApiPo.subtotal`, `ApiPo.already_allocated_total`, `ApiInvoice.amount`, `AllocationInput`.
- Produces: total mode emits `onSubmit({ allocations, nonPoLines: [], referencePoId: null })` where `allocations` are header-level (`po_line_id: null`), one per allocated PO, `invoice_line_id` = invoice's first line id anchor.

- [ ] **Step 1: Add `already_allocated_total` to `ApiPo`**

In `epms/src/services/po.ts`, `interface ApiPo` (~44), add (Decimal → string):
```typescript
  already_allocated_total?: string | null
```

- [ ] **Step 2: Create the total-value panel**

Create `epms/src/pages/invoices/InvoiceTotalMatchPanel.tsx`:
```tsx
import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { formatAmount } from '@/lib/utils'
import type { ApiInvoice, AllocationInput } from '@/services/invoices'
import type { ApiPo } from '@/services/po'

// poId -> amount allocated to that PO from THIS invoice (this session)
type TotalAssign = Record<string, number>

interface Props {
  invoice: ApiInvoice
  pos: ApiPo[]
  onSubmit: (payload: { allocations: AllocationInput[]; nonPoLines: []; referencePoId: null }) => void
  submitting?: boolean
}

export function InvoiceTotalMatchPanel({ invoice, pos, onSubmit, submitting }: Props) {
  const currency = invoice.currency
  const total = Number(invoice.amount)                       // pre-tax
  const anchorLineId = invoice.line_items?.[0]?.id ?? ''
  const [assign, setAssign] = useState<TotalAssign>({})
  const [dragging, setDragging] = useState(false)

  const allocatedSum = useMemo(
    () => Object.values(assign).reduce((s, n) => s + n, 0), [assign])
  const unallocated = total - allocatedSum
  const balanced = Math.abs(unallocated) < 0.01

  const poRemaining = (po: ApiPo) => {
    const otherInvoices = Number(po.already_allocated_total ?? 0)
    const here = assign[po.id] ?? 0
    return Number(po.subtotal) - otherInvoices - here
  }

  const dropOnPo = (po: ApiPo) => {
    setDragging(false)
    if (po.id in assign) return                              // already allocated; edit inline instead
    const remainingBillable = Number(po.subtotal) - Number(po.already_allocated_total ?? 0)
    const amount = Math.max(0, Math.min(unallocated, remainingBillable))
    if (amount <= 0) return
    setAssign((prev) => ({ ...prev, [po.id]: Number(amount.toFixed(2)) }))
  }
  const editAmount = (poId: string, raw: string) => {
    const n = Number(raw)
    setAssign((prev) => ({ ...prev, [poId]: Number.isFinite(n) ? n : 0 }))
  }
  const unassign = (poId: string) =>
    setAssign((prev) => { const n = { ...prev }; delete n[poId]; return n })

  const submit = () => onSubmit({
    allocations: Object.entries(assign).map(([po_id, amount]) => ({
      invoice_line_id: anchorLineId, po_id, po_line_id: null,
      allocated_amount: amount, allocated_tax: 0,
    })),
    nonPoLines: [], referencePoId: null,
  })

  const canSubmit = balanced && Object.keys(assign).length > 0

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-2.5">
        <span className="text-xs text-neutral-500">Unallocated balance (pre-tax)</span>
        <span className={`font-mono text-sm font-semibold ${balanced ? 'text-success-600' : 'text-danger-600'}`}>
          {formatAmount(unallocated, currency)}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Whole-invoice drag source */}
        <div className="rounded-xl border border-neutral-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-400">Invoice</h4>
          <div
            draggable
            onDragStart={() => setDragging(true)}
            onDragEnd={() => setDragging(false)}
            className="cursor-grab rounded-lg border border-primary-200 bg-primary-50 px-3 py-3 text-sm">
            <div className="flex justify-between">
              <span className="truncate">{invoice.vendor_invoice_number}</span>
              <span className="font-mono text-xs">{formatAmount(total, currency)}</span>
            </div>
            <span className="mt-1 block text-[10px] text-neutral-400">Pretax total · drag onto a PO to match by total amount</span>
          </div>
        </div>

        {/* PO header drop targets */}
        <div className="rounded-xl border border-neutral-200 bg-white p-4">
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-neutral-400">Purchase orders (drop here)</h4>
          <div className="flex max-h-[55vh] flex-col gap-2 overflow-y-auto pr-1">
            {pos.map((po) => {
              const here = assign[po.id]
              return (
                <div key={po.id}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => dropOnPo(po)}
                  className={`rounded-lg border px-3 py-2 text-sm ${here != null ? 'border-primary-300 bg-primary-50' : `border-dashed border-neutral-300 ${dragging ? 'hover:border-primary-400' : ''}`}`}>
                  <div className="flex justify-between">
                    <span className="truncate font-mono text-xs text-neutral-600">{po.number}</span>
                    <span className="font-mono text-xs">{formatAmount(Number(po.subtotal), po.currency)}</span>
                  </div>
                  <p className="text-[11px] text-neutral-400">
                    Subtotal {formatAmount(Number(po.subtotal), po.currency)} · Remaining {formatAmount(poRemaining(po), po.currency)}
                  </p>
                  {here != null && (
                    <div className="mt-1.5 flex items-center gap-2">
                      <span className="text-[11px] text-neutral-500">Allocate</span>
                      <input type="number" step="0.01" value={here}
                        onChange={(e) => editAmount(po.id, e.target.value)}
                        className="h-6 w-28 rounded border border-neutral-300 bg-white px-2 text-[11px] focus:outline-none focus:ring-1 focus:ring-primary-500" />
                      <button onClick={() => unassign(po.id)} className="text-[11px] text-primary-600 hover:underline">unassign</button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>

      <div className="flex justify-end">
        <Button onClick={submit} disabled={!canSubmit || submitting}>
          {submitting ? 'Matching...' : 'Confirm total-amount match'}
        </Button>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Add the mode toggle to `InvoiceAllocationPanel` and delegate**

In `InvoiceAllocationPanel.tsx`:
- import the new panel: `import { InvoiceTotalMatchPanel } from './InvoiceTotalMatchPanel'`
- add state after the other hooks (~line 30): `const [mode, setMode] = useState<'line' | 'total'>('line')`
- wrap the returned JSX so a segmented toggle renders above the content, and in `total` mode delegate:
```tsx
  return (
    <div className="flex flex-col gap-4">
      <div className="inline-flex self-start rounded-lg border border-neutral-200 bg-neutral-50 p-0.5 text-xs">
        <button onClick={() => setMode('line')}
          className={`rounded-md px-3 py-1 ${mode === 'line' ? 'bg-white shadow-sm font-medium' : 'text-neutral-500'}`}>By line</button>
        <button onClick={() => setMode('total')}
          className={`rounded-md px-3 py-1 ${mode === 'total' ? 'bg-white shadow-sm font-medium' : 'text-neutral-500'}`}>By total amount</button>
      </div>
      {mode === 'total' ? (
        <InvoiceTotalMatchPanel invoice={invoice} pos={pos} onSubmit={onSubmit} submitting={submitting} />
      ) : (
        <>
          {/* existing line-mode content, unchanged, moves inside this fragment */}
        </>
      )}
    </div>
  )
```
Move the existing panel body (the "Unallocated balance" block through the context-menu `createPortal`) verbatim inside the `mode === 'line'` fragment. Do not change its logic.

- [ ] **Step 4: Add always-on pre-tax headers to the line view**

Within the line-mode fragment, enrich the two column headers:
- Invoice lines `<h4>`: append ` · Pretax total {formatAmount(total, currency)}`.
- Each PO group: below `<p>{po.number}</p>`, add a line showing subtotal and remaining:
```tsx
                <p className="mb-1 text-[11px] text-neutral-400">
                  Subtotal {formatAmount(Number(po.subtotal), po.currency)} · Remaining {formatAmount(Number(po.subtotal) - Number(po.already_allocated_total ?? 0), po.currency)}
                </p>
```
(Purely informational; does not change allocation logic.)

- [ ] **Step 5: Type-check + commit**

```bash
npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"   # expect 59
git add epms/src/pages/invoices/InvoiceTotalMatchPanel.tsx epms/src/pages/invoices/InvoiceAllocationPanel.tsx epms/src/services/po.ts
git commit -m "feat(invoice): By total amount match mode for price-less invoices"
```

---

### Task 6: Memory sync + report (no code)

- [ ] **Step 1: Update memory** — append to (or create) a memory noting: total-value match mode + auto-match-on-upload + uploader self-match shipped on `feature/invoice-match-po-improvements`, zero migrations, not yet merged/deployed; the `invoice_upload` permission caveat (only ap_clerk by default — #4 benefits whoever prod grants upload to); backend stayed on the existing `match`/`allocations` path with the one new `already_allocated_total` field.
- [ ] **Step 2: Report** — summarize to the user; note live browser smoke deferred to integration (dev stack mounts main checkout), and that release awaits their go per R4/R5.

---

## Self-Review Notes

- **Spec coverage:** #4 auth+review (T1) + frontend visibility (T2); #2 backend field (T3) + total-mode UI (T5); #3 auto-match (T4). All covered.
- **Ordering/deps:** T4 relies on T1 (uploader can match) — same release. T5 relies on T3 (`already_allocated_total`).
- **No backend match() change:** total mode and auto-match both reuse existing `allocations`/legacy `po_id` paths; only auth (T1) and the candidates field (T3) touch backend. Fee-only `if allocs/else` branch untouched.
- **Type consistency:** `onSubmit` payload shape `{ allocations, nonPoLines, referencePoId }` — the total panel emits `nonPoLines: []`, `referencePoId: null`, matching the existing consumers (`MatchPanel`, `InvoiceListPage`) with no change. `already_allocated_total` is `string|null` on the wire (Decimal), `Number()`-ed in the panel.
- **tsc:** every frontend task ends on the 59 gate.
