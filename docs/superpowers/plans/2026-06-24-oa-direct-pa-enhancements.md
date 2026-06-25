# OA Direct-PA Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use the matched DB vendor for Direct-PA Title/Vendor, filter Cost Centers by the user's department, allow editing Draft/Returned Direct PAs, and add attachment upload/download on the PA detail page.

**Architecture:** One backend task adds a `PATCH /pa/{id}` update endpoint (+ schema + crud) in expense-api. Three frontend tasks change the OA create wizard, add a new edit page, and add attachment management to the detail page. All attachment binaries continue to live on file-api; the DB stores only the `storage_key`.

**Tech Stack:** FastAPI + SQLAlchemy async + pytest (expense-api); React + TypeScript 6.0.3 + @tanstack/react-query + Vite (oa).

## Global Constraints

- No git commits / pushes / branches this round — all changes stay in the working tree.
- UI strings English-only.
- All attachment binaries on file-api (:8005); DB stores only metadata + `storage_key`. Do NOT introduce any binary-in-DB path.
- Frontend typecheck (run in `uniops/oa/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`.
- Backend tests run against the `expense_test` DB per the project convention (conftest derives `expense_test` from `settings.database_url`; the local docker Postgres must be reachable via the expense-api `POSTGRES_*`/`DATABASE_URL` env, and `expense_test` created once via `python -m scripts.create_test_db`). See memory `feedback_uniops_admin_test_db_env`.
- Edit scope is Payment-Details fields only — invoice OCR data and amounts are NOT editable.

---

### Task 1: Backend — `PATCH /pa/{id}` to edit a Draft/Returned Direct PA

**Files:**
- Modify: `uniops/expense-api/app/schemas/pa.py`
- Modify: `uniops/expense-api/app/crud/pa.py`
- Modify: `uniops/expense-api/app/api/v1/pa.py`
- Test: `uniops/expense-api/tests/test_pa.py`

**Interfaces:**
- Consumes: `pa_crud.get_by_id(db, pa_id)` (existing), `PaResponse` (existing).
- Produces:
  - `PaResponse` now includes `cost_center_id: uuid.UUID | None`.
  - `PaDirectUpdate` schema with optional `title`, `vendor_id`, `vendor_name`, `budget_account_code`, `cost_center_id`, `notes`.
  - `pa_crud.update_direct_pa(db, pa, changes: dict) -> PaymentApplication`.
  - `PATCH /api/v1/pa/{pa_id}` → 200 `PaResponse`; 409 wrong status/type; 403 non-owner; 404 missing.

- [ ] **Step 1: Write failing tests**

Append to `uniops/expense-api/tests/test_pa.py`:

```python
# ── Draft edit (PATCH /pa/{id}) ──────────────────────────────────────────────

async def _set_pa_status(pa_id: str, status: str, pa_type: str | None = None):
    """Directly set a PA's status (and optionally pa_type) in the test DB."""
    async with db_module.AsyncSessionLocal() as db:
        pa = await db.get(PaymentApplication, uuid.UUID(pa_id))
        pa.status = status
        if pa_type is not None:
            pa.pa_type = pa_type
        await db.commit()


@pytest.mark.asyncio
async def test_patch_draft_pa_updates_fields(requester_client):
    inv = await _make_reviewed_invoice(requester_client)
    pa = (await requester_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()
    resp = await requester_client.patch(
        f"/api/v1/pa/{pa['id']}",
        json={"title": "Edited Title", "notes": "edited note"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Edited Title"
    assert body["notes"] == "edited note"


@pytest.mark.asyncio
async def test_patch_returned_pa_allowed(requester_client):
    inv = await _make_reviewed_invoice(requester_client)
    pa = (await requester_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()
    await _set_pa_status(pa["id"], "returned")
    resp = await requester_client.patch(f"/api/v1/pa/{pa['id']}", json={"title": "After Return"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "After Return"


@pytest.mark.asyncio
async def test_patch_submitted_pa_rejected(requester_client):
    inv = await _make_reviewed_invoice(requester_client)
    pa = (await requester_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()
    await _set_pa_status(pa["id"], "submitted")
    resp = await requester_client.patch(f"/api/v1/pa/{pa['id']}", json={"title": "Nope"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_patch_pa_po_rejected(requester_client):
    inv = await _make_reviewed_invoice(requester_client)
    pa = (await requester_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()
    await _set_pa_status(pa["id"], "draft", pa_type="PA-PO")
    resp = await requester_client.patch(f"/api/v1/pa/{pa['id']}", json={"title": "Nope"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_patch_non_owner_rejected(requester_client, requester_client_b):
    inv = await _make_reviewed_invoice(requester_client)
    pa = (await requester_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()
    resp = await requester_client_b.patch(f"/api/v1/pa/{pa['id']}", json={"title": "Hijack"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_patch_pa_not_found(requester_client):
    resp = await requester_client.patch(f"/api/v1/pa/{uuid.uuid4()}", json={"title": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pa_response_exposes_cost_center_id(requester_client):
    inv = await _make_reviewed_invoice(requester_client)
    cc_id = str(uuid.uuid4())
    pa = (await requester_client.post(
        "/api/v1/pa/direct", json=_pa_payload(inv["id"], cost_center_id=cc_id),
    )).json()
    assert pa["cost_center_id"] == cc_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run (in `uniops/expense-api/`, with the test-DB env configured): `pytest tests/test_pa.py -k "patch or cost_center_id" -v`
Expected: FAIL — `test_pa_response_exposes_cost_center_id` fails on missing key; the `patch` tests get 405 (Method Not Allowed) since no PATCH route exists.

- [ ] **Step 3: Add `cost_center_id` to `PaResponse` and the `PaDirectUpdate` schema**

In `uniops/expense-api/app/schemas/pa.py`, add `cost_center_id` to `PaResponse` right after `budget_account_code`:

```python
    budget_account_code: str | None
    cost_center_id: uuid.UUID | None = None
```

And append a new schema after `PaDirectCreate`:

```python
class PaDirectUpdate(BaseModel):
    """Partial update for a Draft/Returned PA-DIR — Payment-Details fields only."""
    title: str | None = None
    vendor_id: uuid.UUID | None = None
    vendor_name: str | None = None
    budget_account_code: str | None = None
    cost_center_id: uuid.UUID | None = None
    notes: str | None = None
```

- [ ] **Step 4: Add `update_direct_pa` to crud**

In `uniops/expense-api/app/crud/pa.py`, append:

```python
async def update_direct_pa(db: AsyncSession, pa: PaymentApplication, changes: dict) -> PaymentApplication:
    """Apply the given field changes to a PA and persist."""
    for key, value in changes.items():
        setattr(pa, key, value)
    await db.flush()
    await db.refresh(pa)
    return pa
```

- [ ] **Step 5: Add the `PATCH /pa/{pa_id}` endpoint**

In `uniops/expense-api/app/api/v1/pa.py`, update the schema import line to include `PaDirectUpdate`:

```python
from app.schemas.pa import PaActionRequest, PaDirectCreate, PaDirectUpdate, PaListResponse, PaResponse, PaymentRecord
```

Then add this handler immediately after `create_direct_pa` (before `get_pa`):

```python
@router.patch("/{pa_id}", response_model=PaResponse)
async def patch_direct_pa(
    pa_id: uuid.UUID,
    body: PaDirectUpdate,
    db: SessionDep,
    user: CurrentUserDep,
):
    """Edit a Draft/Returned PA-DIR — owner (or system_admin) only, Payment-Details fields."""
    pa = await pa_crud.get_by_id(db, pa_id)
    if not pa:
        raise HTTPException(status_code=404, detail="PA not found")
    if pa.pa_type != "PA-DIR" or pa.status not in ("draft", "returned"):
        raise HTTPException(status_code=409, detail="Only draft or returned direct PAs can be edited")
    user_id = uuid.UUID(user["sub"])
    if pa.created_by != user_id and user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="Only the owner can edit this PA")

    changes = body.model_dump(exclude_unset=True)
    if "title" in changes:
        title = (changes["title"] or "").strip()
        if title:
            changes["title"] = title
        else:
            changes.pop("title")  # ignore blank title — keep existing

    await pa_crud.update_direct_pa(db, pa, changes)
    return PaResponse.model_validate(pa)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_pa.py -k "patch or cost_center_id" -v`
Expected: PASS (7 tests). Then run the full PA suite to check no regression: `pytest tests/test_pa.py -v` → all PASS.

- [ ] **Step 7: (No commit — leave changes in working tree per Global Constraints.)**

---

### Task 2: Frontend — Matched-vendor name + dept-filtered Cost Center + create-upload fix

**Files:**
- Modify: `uniops/oa/src/pages/pa/PaDirectCreatePage.tsx`

**Interfaces:**
- Consumes: `useOaAuth` from `@/store/auth` (`user.department_id`), `api.postForm` from `@/lib/api`.
- Produces: `Step2Review` confirm now carries the matched vendor name; `Step3PaForm` accepts a `vendorName` prop and renders Vendor Name read-only.

- [ ] **Step 1: Pass the matched vendor name out of Step 2**

In `PaDirectCreatePage.tsx`, change the `Step2Review` `onConfirmed` prop type (around line 293):

```tsx
  onConfirmed: (fields: ParsedInvoiceFields, vendorId: string | null, vendorName: string | null) => void
```

And update `handleConfirm` (the `onConfirmed(out, selectedVendorId)` call, ~line 387):

```tsx
    onConfirmed(out, selectedVendorId, selectedVendor?.name ?? null)
```

- [ ] **Step 2: Thread `vendorName` through the page-level state**

In `PaDirectCreatePage` (main component, ~line 996), add state and pass it through:

```tsx
  const [vendorId, setVendorId] = useState<string | null>(null)
  const [vendorName, setVendorName] = useState<string | null>(null)
```

Update the Step 2 render's `onConfirmed` (~line 1029):

```tsx
            onConfirmed={(updated, vid, vname) => { setFields(updated); setVendorId(vid); setVendorName(vname); setStep(3) }}
```

Update the Step 3 render (~line 1040) to pass the prop:

```tsx
          <Step3PaForm file={file} fields={fields} vendorId={vendorId} vendorName={vendorName} onCreated={id => navigate(`/pa/${id}`)} />
```

- [ ] **Step 3: Use the matched vendor name in Step 3 (Title default + read-only Vendor Name)**

In `Step3PaForm`'s prop type (~line 676) add `vendorName`:

```tsx
function Step3PaForm({
  file, fields, vendorId, vendorName, onCreated,
}: {
  file: File
  fields: ParsedInvoiceFields
  vendorId: string | null
  vendorName: string | null
  onCreated: (paId: string) => void
}) {
```

Replace the `title` default and remove the editable `vendorName` state (~lines 709-712). Change:

```tsx
  const [title, setTitle] = useState(
    `Direct Payment — ${fields.vendorName ?? 'Vendor'} ${fields.vendorInvoiceNumber ?? ''}`.trim()
  )
  const [vendorName, setVendorName] = useState(fields.vendorName ?? '')
```

to:

```tsx
  const [title, setTitle] = useState(
    `Direct Payment — ${vendorName ?? 'Vendor'} ${fields.vendorInvoiceNumber ?? ''}`.trim()
  )
```

In `handleCreate`, the invoice + PA payloads currently send `vendor_name: vendorName` (the removed state). They now reference the `vendorName` prop directly — no code change needed at those two lines since the identifier is the same, but confirm both `vendor_name: vendorName` usages now resolve to the prop.

Replace the editable Vendor Name block (~lines 871-876) with a read-only display:

```tsx
      {/* Vendor Name — matched DB vendor (read-only) */}
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Vendor Name</label>
        <div className="w-full rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-800">
          {vendorName ?? '—'}
        </div>
      </div>
```

Update the submit button's `disabled` (~line 985) to depend on the prop:

```tsx
      <button type="submit" disabled={saving || !vendorName}
```

- [ ] **Step 4: Filter Cost Centers by the user's department**

Add the auth import near the top of `PaDirectCreatePage.tsx`:

```tsx
import { useOaAuth } from '@/store/auth'
```

In `Step3PaForm`, replace the cost-centers query (~lines 691-694):

```tsx
  const { user } = useOaAuth()
  const deptId = user?.department_id ?? null
  const { data: costCenters = [] } = useQuery<CostCenter[]>({
    queryKey: ['epms-cost-centers', deptId],
    queryFn: () => {
      const qs = deptId
        ? `/api/v1/cost-centers?active_only=true&department_id=${deptId}`
        : '/api/v1/cost-centers?active_only=true'
      return epmsApi.get<CostCenter[]>(qs)
    },
  })
```

- [ ] **Step 5: Fix the attachment upload to use the absolute API client**

In `Step3PaForm.handleCreate`, replace the token lookup + `uploadAttachment` (~lines 751-767) with `api.postForm`:

```tsx
      // 2. Upload invoice file + extra attachments (non-fatal).
      //    Use api.postForm (absolute VITE_API_URL) — OA runs in Docker where the
      //    relative-fetch Vite proxy is dead.
      const uploadAttachment = async (f: File) => {
        const form = new FormData()
        form.append('file', f)
        await api.postForm(`/api/v1/invoice-attachments?invoice_id=${inv.id}&invoice_source=oa`, form)
      }
      await uploadAttachment(file).catch(() => {})

      // 3. Upload extra attachments (non-fatal)
      for (const f of extraFiles) { await uploadAttachment(f).catch(() => {}) }
```

(The `api` import already exists in this file.)

- [ ] **Step 6: Typecheck**

Run (in `uniops/oa/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: PASS, no errors (no unused/`vendorName`-state references remaining).

- [ ] **Step 7: Manual verification**

In the Direct-PA wizard: Step 3 Title defaults to the matched DB vendor name; Vendor Name shows the matched vendor read-only; the Cost Center dropdown lists only the logged-in user's department's cost centers (all, if the user has no department).

---

### Task 3: Frontend — Edit page for Draft/Returned Direct PAs

**Files:**
- Create: `uniops/oa/src/pages/pa/PaDirectEditPage.tsx`
- Modify: `uniops/oa/src/App.tsx`
- Modify: `uniops/oa/src/pages/pa/PaDetailPage.tsx`

**Interfaces:**
- Consumes: `PATCH /api/v1/pa/{id}` and `cost_center_id` on `PaResponse` (Task 1); `useOaAuth`, `epmsApi`, `budgetApi`, `api` from existing libs.
- Produces: route `/pa/:id/edit`; an Edit button on the detail page for draft/returned owners.

- [ ] **Step 1: Create the edit page**

Create `uniops/oa/src/pages/pa/PaDirectEditPage.tsx`:

```tsx
import { useState, useEffect, type FormEvent } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react'
import { api, epmsApi, budgetApi } from '@/lib/api'
import { useOaAuth } from '@/store/auth'

interface Pa {
  id: string; pa_number: string; title: string; status: string
  vendor_id: string; vendor_name: string
  budget_account_code: string | null; cost_center_id: string | null
  notes: string | null
}
interface DbVendor { id: string; name: string; code: string }
interface CostCenter { id: string; code: string; name: string; is_active: boolean }
interface BudgetL2 { id: string; code: string; name: string }
interface BudgetL1 { id: string; code: string; name: string; accounts: BudgetL2[] }

export default function PaDirectEditPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { user } = useOaAuth()
  const deptId = user?.department_id ?? null

  const { data: pa, isLoading } = useQuery<Pa>({
    queryKey: ['pa', id],
    queryFn: () => api.get<Pa>(`/api/v1/pa/${id}`),
    enabled: !!id,
  })

  const { data: costCenters = [] } = useQuery<CostCenter[]>({
    queryKey: ['epms-cost-centers', deptId],
    queryFn: () => epmsApi.get<CostCenter[]>(
      deptId ? `/api/v1/cost-centers?active_only=true&department_id=${deptId}`
             : '/api/v1/cost-centers?active_only=true'),
  })
  const { data: budgetHierarchy } = useQuery<{ l1_groups: BudgetL1[] }>({
    queryKey: ['budget-hierarchy'],
    queryFn: () => budgetApi.get<{ l1_groups: BudgetL1[] }>('/hierarchy'),
  })
  const l1Groups = budgetHierarchy?.l1_groups ?? []

  const [title, setTitle] = useState('')
  const [vendorId, setVendorId] = useState('')
  const [vendorName, setVendorName] = useState('')
  const [selectedCostCenterId, setSelectedCostCenterId] = useState('')
  const [selectedL1, setSelectedL1] = useState('')
  const [selectedL2, setSelectedL2] = useState('')
  const [notes, setNotes] = useState('')
  const [vendorSearch, setVendorSearch] = useState('')
  const [vendorSearchOpen, setVendorSearchOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // Prefill once the PA and budget hierarchy load.
  useEffect(() => {
    if (!pa) return
    setTitle(pa.title)
    setVendorId(pa.vendor_id)
    setVendorName(pa.vendor_name)
    setSelectedCostCenterId(pa.cost_center_id ?? '')
    setNotes(pa.notes ?? '')
  }, [pa])
  useEffect(() => {
    if (!pa?.budget_account_code || l1Groups.length === 0) return
    const l1 = l1Groups.find(g => g.accounts.some(a => a.code === pa.budget_account_code))
    if (l1) { setSelectedL1(l1.code); setSelectedL2(pa.budget_account_code!) }
  }, [pa, l1Groups])

  const { data: vendorsData } = useQuery<{ items: DbVendor[] }>({
    queryKey: ['vendors-search', vendorSearch],
    queryFn: () => {
      const p = new URLSearchParams({ active_only: 'true', page_size: '50' })
      if (vendorSearch) p.set('search', vendorSearch)
      return api.get<{ items: DbVendor[] }>(`/api/v1/vendors?${p}`)
    },
    enabled: vendorSearchOpen,
  })
  const vendors = vendorsData?.items ?? []
  const l2Accounts = l1Groups.find(l1 => l1.code === selectedL1)?.accounts ?? []

  if (isLoading) return (
    <div className="flex items-center justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-neutral-400" /></div>
  )
  if (!pa) return <div className="py-16 text-center text-sm text-red-500">Payment application not found</div>
  if (!['draft', 'returned'].includes(pa.status)) return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <a href={`/pa/${id}`} className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700">
        <ArrowLeft className="h-4 w-4" />Back to PA
      </a>
      <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
        This payment application is {pa.status} and can no longer be edited.
      </p>
    </div>
  )

  const save = async (e: FormEvent) => {
    e.preventDefault()
    setSaving(true); setError('')
    try {
      await api.patch(`/api/v1/pa/${id}`, {
        title: title.trim(),
        vendor_id: vendorId || null,
        vendor_name: vendorName || null,
        budget_account_code: selectedL2 || null,
        cost_center_id: selectedCostCenterId || null,
        notes: notes || null,
      })
      navigate(`/pa/${id}`)
    } catch (err: any) {
      setError(err.message || 'Failed to save changes')
    } finally { setSaving(false) }
  }

  const input = 'w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400'
  const sel = 'h-9 w-full rounded-md border border-neutral-200 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500'

  return (
    <form onSubmit={save} className="flex flex-col gap-5 max-w-2xl">
      <div>
        <a href={`/pa/${id}`} className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 mb-4">
          <ArrowLeft className="h-4 w-4" />Back to PA
        </a>
        <h1 className="text-2xl font-bold text-neutral-900">Edit Payment Application</h1>
        <p className="mt-0.5 text-sm text-neutral-500 font-mono">{pa.pa_number}</p>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Title *</label>
        <input value={title} onChange={e => setTitle(e.target.value)} required className={input} />
      </div>

      {/* Matched Vendor */}
      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Matched Vendor</label>
        <div className="flex items-center justify-between gap-2 rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2">
          <span className="text-sm text-neutral-800">{vendorName || '—'}</span>
          <button type="button" onClick={() => { setVendorSearchOpen(o => !o); setVendorSearch('') }}
            className="text-xs text-neutral-400 hover:text-neutral-600 underline shrink-0">Change</button>
        </div>
        {vendorSearchOpen && (
          <div className="relative mt-1">
            <input autoFocus placeholder="Search vendor by name or code…" value={vendorSearch}
              onChange={e => setVendorSearch(e.target.value)} className={input} />
            <div className="absolute z-20 mt-1 left-0 right-0 rounded-lg border border-neutral-200 bg-white shadow-lg max-h-48 overflow-y-auto">
              {vendors.length ? vendors.map(v => (
                <button key={v.id} type="button"
                  onClick={() => { setVendorId(v.id); setVendorName(v.name); setVendorSearchOpen(false) }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-primary-50 text-left">
                  <span className="font-mono text-xs bg-neutral-100 rounded px-1.5 py-0.5 text-neutral-600 shrink-0">{v.code}</span>
                  {v.name}
                </button>
              )) : <p className="px-3 py-2 text-xs text-neutral-400">No vendors match your search</p>}
            </div>
          </div>
        )}
      </div>

      {/* Cost Center & Budget Account */}
      <div className="rounded-xl border border-neutral-200 bg-white p-4 flex flex-col gap-2">
        <label className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Cost Center & Budget Account</label>
        <select value={selectedCostCenterId} onChange={e => setSelectedCostCenterId(e.target.value)} className={sel}>
          <option value="">Select Cost Center…</option>
          {costCenters.map(cc => <option key={cc.id} value={cc.id}>{cc.code} — {cc.name}</option>)}
        </select>
        <select value={selectedL1} onChange={e => { setSelectedL1(e.target.value); setSelectedL2('') }} className={sel}>
          <option value="">Select L1 Category…</option>
          {l1Groups.map(l1 => <option key={l1.id} value={l1.code}>{l1.code} — {l1.name}</option>)}
        </select>
        <select value={selectedL2} onChange={e => setSelectedL2(e.target.value)} disabled={!selectedL1}
          className={`${sel} disabled:bg-neutral-50 disabled:text-neutral-400`}>
          <option value="">Select L2 Account…</option>
          {l2Accounts.map(a => <option key={a.id} value={a.code}>{a.code} — {a.name}</option>)}
        </select>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Notes</label>
        <textarea value={notes} onChange={e => setNotes(e.target.value)} rows={2} className={`${input} resize-none`} />
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
          <AlertTriangle className="h-4 w-4 shrink-0" />{error}
        </div>
      )}

      <button type="submit" disabled={saving || !title.trim()}
        className="flex items-center justify-center gap-2 rounded-lg bg-[#085E5E] px-4 py-2.5 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50 transition-colors">
        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
        {saving ? 'Saving…' : 'Save Changes'}
      </button>
    </form>
  )
}
```

- [ ] **Step 2: Register the route**

In `uniops/oa/src/App.tsx`, add the import:

```tsx
import PaDirectEditPage from '@/pages/pa/PaDirectEditPage'
```

And add the route inside the Payment Applications group (after the `/pa/new/direct` route):

```tsx
            <Route path="/pa/:id/edit" element={<PaDirectEditPage />} />
```

- [ ] **Step 3: Add the Edit button on the detail page**

In `uniops/oa/src/pages/pa/PaDetailPage.tsx`, in `ActionArea`, the `(status === 'draft' || status === 'returned') && isOwner` branch (~line 136), add an Edit link as the first button inside the `flex flex-wrap items-center gap-3` row:

```tsx
          <a href={`/pa/${pa.id}/edit`}
            className="flex items-center gap-2 rounded-lg border border-neutral-200 bg-white px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors">
            <Pencil className="h-4 w-4" />Edit
          </a>
```

Add `Pencil` to the lucide-react import at the top of the file (the existing import block, ~lines 4-7):

```tsx
  ArrowLeft, Loader2, CheckCircle2, RotateCcw, XCircle,
  AlertTriangle, Paperclip, Clock, Download, FileText, Banknote, Pencil,
```

- [ ] **Step 4: Typecheck**

Run (in `uniops/oa/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: PASS, no errors.

- [ ] **Step 5: Manual verification**

A draft or returned Direct PA shows an Edit button on its detail page; clicking it opens the edit page prefilled (title, vendor, cost center, budget L1/L2, notes); saving persists and returns to the detail page. A submitted/approved PA shows no Edit button, and navigating directly to `/pa/:id/edit` for it shows the "can no longer be edited" message.

---

### Task 4: Frontend — Attachment upload / delete / download on the detail page

**Files:**
- Modify: `uniops/oa/src/lib/api.ts`
- Modify: `uniops/oa/src/pages/pa/PaDetailPage.tsx`

**Interfaces:**
- Consumes: existing `POST/GET/DELETE /api/v1/invoice-attachments`, `GET /api/v1/invoice-attachments/{id}/file`.
- Produces: `api.getBlob(path): Promise<Blob>`; an upload+delete+authenticated-download UI in `AttachmentsTab`.

- [ ] **Step 1: Add `getBlob` to the API client**

In `uniops/oa/src/lib/api.ts`, add this function after `postForm` (before the `export const api` block):

```ts
// Authenticated binary fetch — needed for file downloads, since an <a href> to an
// auth-required endpoint carries no bearer token and (in prod) points at the wrong origin.
async function getBlob(path: string): Promise<Blob> {
  const h: Record<string, string> = {}
  const token = getToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${BASE}${path}`, { headers: h })
  if (res.status === 401) {
    localStorage.removeItem('oa-auth')
    window.location.href = `${PORTAL_URL}/logout`
    throw new Error('Session expired. Redirecting to portal…')
  }
  if (!res.ok) throw await toApiError(res)
  return res.blob()
}
```

And add it to the exported `api` object:

```ts
export const api = {
  get:    <T>(path: string)             => request<T>('GET', path),
  post:   <T>(path: string, body: unknown) => request<T>('POST', path, body),
  patch:  <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  delete: <T>(path: string)             => request<T>('DELETE', path),
  postForm,
  getBlob,
}
```

- [ ] **Step 2: Rewrite `AttachmentsTab` with upload, delete, and authenticated download**

In `uniops/oa/src/pages/pa/PaDetailPage.tsx`, the `AttachmentsTab` component needs `perms` (to gate owner-only controls) and `queryClient` (to refetch). First, update its call site (~line 853) to pass `perms`:

```tsx
          {activeTab === 'Attachments' && <AttachmentsTab pa={pa} perms={perms} />}
```

Then replace the whole `AttachmentsTab` function (~lines 581-638) with:

```tsx
function AttachmentsTab({ pa, perms }: { pa: Pa; perms: PaPermissions | undefined }) {
  const queryClient = useQueryClient()
  const invoiceId = pa.invoice_ids[0] ?? null
  const isOwner = perms?.is_owner ?? false
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { data: attachments = [], isLoading } = useQuery<InvoiceAttachment[]>({
    queryKey: ['pa-attachments', pa.id],
    queryFn: async () => {
      if (!invoiceId) return []
      return api.get<InvoiceAttachment[]>(`/api/v1/invoice-attachments?invoice_id=${invoiceId}`)
    },
    enabled: !!invoiceId,
  })

  const refetch = () => queryClient.invalidateQueries({ queryKey: ['pa-attachments', pa.id] })

  const upload = async (files: FileList) => {
    if (!invoiceId) { setError('No linked invoice to attach to.'); return }
    setBusy(true); setError('')
    try {
      for (const f of Array.from(files)) {
        const form = new FormData(); form.append('file', f)
        await api.postForm(`/api/v1/invoice-attachments?invoice_id=${invoiceId}&invoice_source=oa`, form)
      }
      await refetch()
    } catch (e: any) {
      setError(e.message || 'Upload failed')
    } finally { setBusy(false) }
  }

  const remove = async (attId: string) => {
    setBusy(true); setError('')
    try {
      await api.delete(`/api/v1/invoice-attachments/${attId}`)
      await refetch()
    } catch (e: any) {
      setError(e.message || 'Delete failed')
    } finally { setBusy(false) }
  }

  const download = async (att: InvoiceAttachment) => {
    try {
      const blob = await api.getBlob(`/api/v1/invoice-attachments/${att.id}/file`)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = att.file_name; a.click()
      URL.revokeObjectURL(url)
    } catch (e: any) {
      setError(e.message || 'Download failed')
    }
  }

  function formatSize(bytes: number) {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  return (
    <div className="flex flex-col gap-3 pt-5">
      {isOwner && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-dashed border-neutral-300 px-4 py-3">
          <p className="text-sm text-neutral-500">Add supporting documents (agreements, acceptance docs, etc.)</p>
          <button type="button" disabled={busy || !invoiceId} onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50 transition-colors">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}Upload
          </button>
          <input ref={fileInputRef} type="file" multiple className="hidden"
            onChange={e => { if (e.target.files?.length) upload(e.target.files); e.target.value = '' }} />
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
          <AlertTriangle className="h-4 w-4 shrink-0" />{error}
        </div>
      )}

      {isLoading ? (
        <div className="flex items-center justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-neutral-400" /></div>
      ) : attachments.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-neutral-100 mb-3">
            <Paperclip className="h-5 w-5 text-neutral-400" />
          </div>
          <p className="text-sm text-neutral-500">No attachments</p>
        </div>
      ) : (
        attachments.map(att => (
          <div key={att.id} className="flex items-center gap-3 rounded-lg border border-neutral-200 bg-white px-4 py-3">
            <Paperclip className="h-4 w-4 text-neutral-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-neutral-800 truncate">{att.file_name}</p>
              <p className="text-xs text-neutral-400">{formatSize(att.file_size_bytes)}</p>
            </div>
            <button type="button" onClick={() => download(att)}
              className="flex items-center gap-1 text-xs font-medium text-primary-600 hover:text-primary-800 shrink-0">
              <Download className="h-3.5 w-3.5" />Download
            </button>
            {isOwner && (
              <button type="button" disabled={busy} onClick={() => remove(att.id)}
                className="flex items-center gap-1 text-xs font-medium text-red-500 hover:text-red-700 disabled:opacity-50 shrink-0">
                <XCircle className="h-3.5 w-3.5" />Delete
              </button>
            )}
          </div>
        ))
      )}
    </div>
  )
}
```

This uses `useRef` — ensure `useRef` is imported. Update the React import at the top (`import { useState } from 'react'`, ~line 1) to:

```tsx
import { useState, useRef } from 'react'
```

(`useQueryClient`, `Download`, `Paperclip`, `XCircle`, `AlertTriangle`, `Loader2` are already imported in this file.)

- [ ] **Step 3: Typecheck**

Run (in `uniops/oa/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: PASS, no errors.

- [ ] **Step 4: Manual verification**

On a Direct PA detail page as the owner: the Attachments tab shows an Upload control; uploading a file stores it on file-api and the row appears; Download fetches the file with auth and saves it; Delete removes it. A non-owner viewing the tab sees download only (no upload/delete).

---

## Notes

- No commit steps: per the project standing instruction, leave all changes in the working tree for a later batch commit.
- Task order: Task 1 (backend) first — Tasks 3 relies on the `PATCH` endpoint and `cost_center_id` field. Tasks 2 and 4 are independent of Task 1 and of each other.
- Pre-existing `InvoiceAttachment` frontend type uses `mime_type` (backend returns `content_type`); it is unused for rendering, so no change is required.
