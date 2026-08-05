# QBO Vendor Email → EPMS Remittance Email Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A manual "Fill Remittance Emails" action on the Finance QBO Mirror page that copies `qbo_vendors.email` into empty `business_partners.remittance_email` by normalized-name match, with a full result report.

**Architecture:** One new finance-api endpoint (`POST /qbo/vendor-emails/backfill`) does a set-based, single-transaction, fill-only update joining the QBO vendor mirror to the shared `business_partners` table (same Postgres DB; finance-api already has a mirror model). The finance frontend adds a button + confirm dialog + result dialog on the QBO Mirror page's Vendors tab. No migration, no schema change.

**Tech Stack:** FastAPI + SQLAlchemy async (finance-api), React + TanStack Query + Tailwind (finance frontend), pytest + local Postgres `finance_test` DB.

**Spec:** `docs/superpowers/specs/2026-08-04-qbo-vendor-remittance-email-design.md`

## Global Constraints

- Branch `feature/qbo-vendor-remittance-email`, worktree `c:/Project/uniops-qbo-email`. Never work in `c:/Project/uniops` (detached HEAD, other sessions' state).
- Matching: case-insensitive + whitespace-trimmed exact match on `business_partners.name` ↔ `qbo_vendors.display_name`. No fuzzy matching.
- Fill-only: update `remittance_email` only when `NULL` or blank/whitespace string. Never overwrite.
- Ambiguous names (either side) are skipped and reported — never guessed.
- Auth: `CurrentUser` only (server-side), consistent with every other `/qbo/*` endpoint.
- All UI copy in English.
- Backend tests run on host against local docker Postgres (`finance_test` on `localhost:5432`, defaults built into `finance-api/tests/conftest.py`). Do NOT point anything at 10.10.50.20.
- Verification needs positive evidence: run the test file BEFORE changes to establish the baseline pass count, compare after.

---

### Task 1: Backend — `POST /qbo/vendor-emails/backfill` endpoint

**Files:**
- Modify: `finance-api/app/api/v1/qbo.py` (add import + one endpoint, place after the `trigger_sync` endpoint, before the "entity browse + detail" section)
- Test: `finance-api/tests/test_qbo_api.py` (append two tests at the end)

**Interfaces:**
- Consumes: existing models `app.models.qbo.QboVendor` (cols: `qbo_id`, `display_name`, `email`, `deleted_at`, `raw` NOT NULL) and `app.models.mirrors.BusinessPartner` (table `business_partners`; cols: `code`, `name`, `contact_email` NOT NULL; `remittance_email` nullable; `is_supplier` bool).
- Produces: `POST /finance/v1/qbo/vendor-emails/backfill` returning
  `{"updated": [{"code","name","email"}], "skipped_has_value": int, "ambiguous": [{"side": "qbo"|"epms", "name": str}], "unmatched_qbo": [str]}`.
  Task 2's frontend types must match this shape exactly.

- [ ] **Step 1: Establish the baseline**

Run: `cd c:/Project/uniops-qbo-email/finance-api && python -m pytest tests/test_qbo_api.py -v`
Expected: all existing tests PASS (record the count — currently 10). If the local docker Postgres (`uniops_postgres`) is not running, start it first; do not proceed on a red baseline.

- [ ] **Step 2: Write the two failing tests**

Append to `finance-api/tests/test_qbo_api.py`:

```python
# ── vendor email backfill ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vendor_email_backfill_fills_empty_and_reports(db_session):
    from sqlalchemy import select

    from app.models.mirrors import BusinessPartner
    from app.models.qbo import QboVendor

    db_session.add_all([
        # case/whitespace-insensitive match, NULL remittance -> filled
        QboVendor(qbo_id="v1", display_name="  Acme Ltd ", email="ap@acme.com", raw={}),
        BusinessPartner(code="V001", name="ACME LTD", contact_email="c@acme.com",
                        remittance_email=None, is_supplier=True),
        # blank-string remittance ("" is how the EPMS frontend persists empty) -> filled
        QboVendor(qbo_id="v2", display_name="Beta Inc", email="pay@beta.com", raw={}),
        BusinessPartner(code="V002", name="beta inc", contact_email="c@beta.com",
                        remittance_email="", is_supplier=True),
        # human-entered value -> never overwritten, counted as skipped
        QboVendor(qbo_id="v3", display_name="Gamma Co", email="new@gamma.com", raw={}),
        BusinessPartner(code="V003", name="Gamma Co", contact_email="c@gamma.com",
                        remittance_email="keep@gamma.com", is_supplier=True),
        # QBO vendor with no EPMS supplier -> unmatched worklist
        QboVendor(qbo_id="v4", display_name="Delta LLC", email="d@delta.com", raw={}),
    ])
    await db_session.flush()

    _override_auth_and_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/finance/v1/qbo/vendor-emails/backfill",
                         headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()

    assert r.status_code == 200
    body = r.json()
    assert {(u["code"], u["email"]) for u in body["updated"]} == {
        ("V001", "ap@acme.com"), ("V002", "pay@beta.com")}
    assert body["skipped_has_value"] == 1
    assert body["ambiguous"] == []
    assert body["unmatched_qbo"] == ["Delta LLC"]

    # persisted, and the human value survived
    rows = (await db_session.execute(
        select(BusinessPartner.code, BusinessPartner.remittance_email)
        .order_by(BusinessPartner.code))).all()
    assert dict(rows) == {"V001": "ap@acme.com", "V002": "pay@beta.com",
                          "V003": "keep@gamma.com"}


@pytest.mark.asyncio
async def test_vendor_email_backfill_skips_ambiguous_deleted_and_empty(db_session):
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models.mirrors import BusinessPartner
    from app.models.qbo import QboVendor

    db_session.add_all([
        # QBO-side ambiguity: same normalized name, different emails -> skip
        QboVendor(qbo_id="a1", display_name="Dup Co", email="one@dup.com", raw={}),
        QboVendor(qbo_id="a2", display_name="dup co", email="two@dup.com", raw={}),
        BusinessPartner(code="V010", name="Dup Co", contact_email="c@dup.com",
                        is_supplier=True),
        # EPMS-side ambiguity: two suppliers share the normalized name -> skip
        QboVendor(qbo_id="b1", display_name="Twin Ltd", email="t@twin.com", raw={}),
        BusinessPartner(code="V011", name="Twin Ltd", contact_email="a@twin.com",
                        is_supplier=True),
        BusinessPartner(code="V012", name="twin ltd", contact_email="b@twin.com",
                        is_supplier=True),
        # soft-deleted QBO vendor: excluded entirely (not even "unmatched")
        QboVendor(qbo_id="c1", display_name="Ghost Inc", email="g@ghost.com",
                  deleted_at=datetime.now(timezone.utc), raw={}),
        BusinessPartner(code="V013", name="Ghost Inc", contact_email="c@ghost.com",
                        is_supplier=True),
        # QBO vendor with blank email: excluded entirely
        QboVendor(qbo_id="d1", display_name="Silent Co", email="  ", raw={}),
        BusinessPartner(code="V014", name="Silent Co", contact_email="c@silent.com",
                        is_supplier=True),
        # partner exists but is_supplier=False -> QBO vendor counts as unmatched
        QboVendor(qbo_id="e1", display_name="Cust Only", email="x@cust.com", raw={}),
        BusinessPartner(code="V015", name="Cust Only", contact_email="c@cust.com",
                        is_supplier=False),
    ])
    await db_session.flush()

    _override_auth_and_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/finance/v1/qbo/vendor-emails/backfill",
                         headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()

    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == []
    assert body["skipped_has_value"] == 0
    # iteration is by sorted normalized name: "dup co" < "twin ltd"
    assert body["ambiguous"] == [{"side": "qbo", "name": "Dup Co"},
                                 {"side": "epms", "name": "Twin Ltd"}]
    assert body["unmatched_qbo"] == ["Cust Only"]

    # nothing was written anywhere
    untouched = (await db_session.execute(
        select(BusinessPartner.remittance_email))).scalars().all()
    assert set(untouched) == {None}
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `cd c:/Project/uniops-qbo-email/finance-api && python -m pytest tests/test_qbo_api.py -k vendor_email_backfill -v`
Expected: 2 FAILED with 404 (`assert r.status_code == 200` fails — route doesn't exist yet).

- [ ] **Step 4: Implement the endpoint**

In `finance-api/app/api/v1/qbo.py`:

Add to the imports block (after the `app.models.qbo` import group):

```python
from app.models.mirrors import BusinessPartner
```

Insert after the `trigger_sync` function (before the "entity browse + detail" comment block):

```python
# ── vendor email backfill ───────────────────────────────────────────────
# POST is safe next to the GET-only /{entity} catch-alls — method+path routing
# means this never shadows browse/detail.

@router.post("/vendor-emails/backfill")
async def backfill_vendor_emails(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Copy qbo_vendors.email into EMPTY business_partners.remittance_email,
    matched on lower(trim(name)) == lower(trim(display_name)). Fill-only
    (human-entered values are never overwritten) and idempotent; ambiguous
    names on either side are skipped and reported, never guessed."""
    def norm(s: str | None) -> str:
        return (s or "").strip().lower()

    qbo_rows = (await db.execute(
        select(QboVendor).where(QboVendor.deleted_at.is_(None))
        .order_by(QboVendor.qbo_id))).scalars().all()
    emails_by_key: dict[str, set[str]] = {}
    display_by_key: dict[str, str] = {}   # first-seen trimmed display name
    for v in qbo_rows:
        key = norm(v.display_name)
        email = (v.email or "").strip()
        if not key or not email:
            continue
        display_by_key.setdefault(key, (v.display_name or "").strip())
        emails_by_key.setdefault(key, set()).add(email)

    partners = (await db.execute(
        select(BusinessPartner).where(BusinessPartner.is_supplier.is_(True))
        .order_by(BusinessPartner.code))).scalars().all()
    partners_by_key: dict[str, list[BusinessPartner]] = {}
    for p in partners:
        key = norm(p.name)
        if key:
            partners_by_key.setdefault(key, []).append(p)

    updated: list[dict] = []
    ambiguous: list[dict] = []
    unmatched: list[str] = []
    skipped_has_value = 0
    for key in sorted(emails_by_key):
        emails = emails_by_key[key]
        if len(emails) > 1:
            ambiguous.append({"side": "qbo", "name": display_by_key[key]})
            continue
        matches = partners_by_key.get(key)
        if not matches:
            unmatched.append(display_by_key[key])
            continue
        if len(matches) > 1:
            ambiguous.append({"side": "epms", "name": matches[0].name.strip()})
            continue
        partner = matches[0]
        if (partner.remittance_email or "").strip():
            skipped_has_value += 1
            continue
        partner.remittance_email = next(iter(emails))
        updated.append({"code": partner.code, "name": partner.name,
                        "email": partner.remittance_email})
    await db.commit()
    return {"updated": updated, "skipped_has_value": skipped_has_value,
            "ambiguous": ambiguous, "unmatched_qbo": unmatched}
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `cd c:/Project/uniops-qbo-email/finance-api && python -m pytest tests/test_qbo_api.py -k vendor_email_backfill -v`
Expected: 2 PASSED.

- [ ] **Step 6: Run the whole file against the baseline**

Run: `cd c:/Project/uniops-qbo-email/finance-api && python -m pytest tests/test_qbo_api.py -v`
Expected: baseline count + 2, all PASS, zero new failures.

- [ ] **Step 7: Commit**

```bash
cd c:/Project/uniops-qbo-email
git add finance-api/app/api/v1/qbo.py finance-api/tests/test_qbo_api.py
git commit -m "feat(finance): backfill EPMS vendor remittance emails from QBO mirror"
```

---

### Task 2: Frontend — button + confirm + result report on the Vendors tab

**Files:**
- Modify: `finance/src/services/qboApi.ts` (result type + API method)
- Modify: `finance/src/pages/finance/QboMirrorPage.tsx` (button in `QboTabs` toolbar, new `VendorEmailBackfill` component)

**Interfaces:**
- Consumes: Task 1's endpoint `POST /qbo/vendor-emails/backfill` (finance base path is prepended by `financeApi`), response shape
  `{updated: {code,name,email}[], skipped_has_value: number, ambiguous: {side,name}[], unmatched_qbo: string[]}`.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Establish the tsc baseline**

```bash
cd c:/Project/uniops-qbo-email/finance
npm ci
npx tsc -p tsconfig.app.json --noEmit
```
Expected: record the error count BEFORE any change (expected 0 on a clean checkout of main; whatever it is, that is the baseline the final check must equal). Note: fresh worktree ⇒ `npm ci` is required or tsc produces bogus module-resolution errors.

- [ ] **Step 2: Add the API type + method**

In `finance/src/services/qboApi.ts`, add after the `QboDetail` interface:

```ts
export interface QboBackfillResult {
  updated: { code: string; name: string; email: string }[]
  skipped_has_value: number
  ambiguous: { side: 'qbo' | 'epms'; name: string }[]
  unmatched_qbo: string[]
}
```

and add to the `qboApi` object (after `detail:`):

```ts
  backfillVendorEmails: () =>
    financeApi.post<QboBackfillResult>('/qbo/vendor-emails/backfill', {}),
```

- [ ] **Step 3: Add the UI**

In `finance/src/pages/finance/QboMirrorPage.tsx`:

1. Extend the qboApi import (line 12) to also bring in the new type:

```ts
import { qboApi, ENTITY_TABS, type QboBackfillResult, type QboDetail } from '@/services/qboApi'
```

2. Extend the lucide import (line 11) with `Mail`:

```ts
import { AlertTriangle, ChevronLeft, ChevronRight, Loader2, Mail, RefreshCw, X } from 'lucide-react'
```

3. In `QboTabs`, inside the toolbar row (the `div.flex.flex-wrap.items-center.gap-2` that wraps the search form), add after the `</form>`:

```tsx
        {tab === 'vendors' && <VendorEmailBackfill />}
```

4. Add the component at the end of the file (after `DetailModal`), following the page's existing inline-overlay dialog pattern:

```tsx
function VendorEmailBackfill() {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<QboBackfillResult | null>(null)

  async function run() {
    setBusy(true)
    setError(null)
    try {
      const r = await qboApi.backfillVendorEmails()
      setResult(r)
      setConfirming(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button className={secondaryBtn} onClick={() => { setError(null); setConfirming(true) }}>
        <Mail className="h-4 w-4" /> Fill Remittance Emails
      </button>

      {confirming && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={() => !busy && setConfirming(false)}>
          <div className="w-full max-w-md space-y-3 rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-800">
              <Mail className="h-4 w-4 text-[#085E5E]" /> Fill Remittance Emails
            </h2>
            <p className="text-sm text-neutral-600">
              Copies each QuickBooks vendor email into the matching EPMS vendor's
              Remittance Email — only where it is currently empty. Existing values
              are never overwritten.
            </p>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 border-t border-neutral-100 pt-3">
              <button className={secondaryBtn} disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
              <button className={primaryBtn} disabled={busy} onClick={run}>
                {busy && <Loader2 className="h-4 w-4 animate-spin" />} Fill emails
              </button>
            </div>
          </div>
        </div>
      )}

      {result && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={() => setResult(null)}>
          <div className="max-h-[85vh] w-full max-w-2xl space-y-4 overflow-auto rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-neutral-100 pb-3">
              <h2 className="text-base font-semibold text-neutral-800">Backfill result</h2>
              <button onClick={() => setResult(null)} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
                <X className="h-5 w-5" />
              </button>
            </div>

            <p className="text-sm text-neutral-600">
              {result.updated.length} filled · {result.skipped_has_value} already had a value ·{' '}
              {result.ambiguous.length} ambiguous · {result.unmatched_qbo.length} unmatched
            </p>

            {result.updated.length > 0 && (
              <div>
                <h3 className="mb-1 text-sm font-semibold text-neutral-700">Filled</h3>
                <div className="overflow-hidden rounded-lg border border-neutral-200">
                  <table className="w-full text-sm">
                    <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                      <tr>
                        <th className="px-3 py-2 font-medium">Code</th>
                        <th className="px-3 py-2 font-medium">Vendor</th>
                        <th className="px-3 py-2 font-medium">Email</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.updated.map((u) => (
                        <tr key={u.code} className="border-t border-neutral-100">
                          <td className="whitespace-nowrap px-3 py-1.5">{u.code}</td>
                          <td className="px-3 py-1.5">{u.name}</td>
                          <td className="px-3 py-1.5">{u.email}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {result.ambiguous.length > 0 && (
              <div>
                <h3 className="mb-1 text-sm font-semibold text-neutral-700">
                  Ambiguous names (skipped — resolve manually)
                </h3>
                <ul className="max-h-40 space-y-0.5 overflow-auto text-sm text-neutral-600">
                  {result.ambiguous.map((a, i) => (
                    <li key={i}>{a.name} <span className="text-xs text-neutral-400">({a.side === 'qbo' ? 'duplicate in QuickBooks' : 'duplicate in EPMS'})</span></li>
                  ))}
                </ul>
              </div>
            )}

            {result.unmatched_qbo.length > 0 && (
              <div>
                <h3 className="mb-1 text-sm font-semibold text-neutral-700">
                  No matching EPMS vendor
                </h3>
                <ul className="max-h-40 space-y-0.5 overflow-auto text-sm text-neutral-600">
                  {result.unmatched_qbo.map((n) => <li key={n}>{n}</li>)}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}
```

- [ ] **Step 4: Verify against the tsc baseline**

Run: `cd c:/Project/uniops-qbo-email/finance && npx tsc -p tsconfig.app.json --noEmit`
Expected: error count equals the Step 1 baseline (no new errors).

- [ ] **Step 5: Commit**

```bash
cd c:/Project/uniops-qbo-email
git add finance/src/services/qboApi.ts finance/src/pages/finance/QboMirrorPage.tsx
git commit -m "feat(finance-ui): Fill Remittance Emails action on QBO Mirror vendors tab"
```
