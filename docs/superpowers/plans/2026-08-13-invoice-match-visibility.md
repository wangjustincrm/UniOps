# Invoice Match Visibility Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make invoice-to-PO/agreement variance visible to PA approvers, deliver the two AP task notifications that never arrive, and reword the review task so it reads as a data confirmation rather than a payment approval.

**Architecture:** Backend adds one computed field (`claimed_receipts`) to `InvoiceResponse` and fixes two task/notification defects in `epms-api`. Frontend adds a collapsible variance panel to PA Detail that branches on the PA's route (PO vs house-account agreement) and, for the PO route, on the match mode (`po_line_id` non-null = by-line, null = by-amount). No database migration.

**Tech Stack:** FastAPI + SQLAlchemy async (epms-api), React + TypeScript + Vite + Tailwind (epms), pytest, TanStack Query.

**Spec:** `docs/superpowers/specs/2026-08-13-ap-payment-officer-and-match-visibility-design.md`

## Global Constraints

- Base branch: `feature/ap-payment-officer-and-match-visibility`, worktree `C:/Project/uniops-payofficer`, based on `origin/main` = `2cbf817`.
- **No database migration in this plan.** `claimed_receipts` is a computed response field.
- **All user-facing UI copy is English.** Code comments may be Chinese.
- `Decimal` is serialised by Pydantic as a JSON **string** — every frontend read must go through `Number()` before arithmetic or comparison.
- Money comparison uses cent-rounded equality (`centsEqual`), never `=== 0` or `!== 0`.
- House-account comparison basis is the invoice's **`total_amount` (tax-inclusive)**, deliberately opposite to the PO allocation route's pre-tax basis. Do not "fix" this.
- Receipts with no amount (`total_amount == null`, i.e. delivery / service types) are **excluded** from the comparison; when no priced receipt is selected the variance is **zero by definition**.
- Frontend shared helpers live in `epms/src/lib/` (this branch — not `utils/`).
- Follow EPMS existing component styling; use shell `Button` rather than a bare `<button>`.

### Running epms-api tests (read this before Task 2)

The container has no pytest — tests run on the **host**. The repo `.env` points at the **production** DB and conftest calls `drop_all`, so the DB env must be overridden, and a worktree has no untracked `.env` so `JWT_SECRET_KEY` must be passed explicitly.

```bash
LP=$(docker exec uniops_postgres printenv POSTGRES_PASSWORD)
cd /c/Project/uniops-payofficer/epms-api

# 1. REFUSE to run unless the target DB is local (conftest will drop_all).
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
  POSTGRES_PASSWORD="$LP" POSTGRES_DB=epms JWT_SECRET_KEY=test-secret \
  python -c "from app.core.config import settings; u=str(settings.DATABASE_URL); \
    assert 'localhost' in u or '127.0.0.1' in u, 'REFUSING: not a local DB'; print('DB OK')"

# 2. Run
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms \
  POSTGRES_PASSWORD="$LP" POSTGRES_DB=epms JWT_SECRET_KEY=test-secret \
  python -m pytest tests/<file>.py -q
```

**Serialise runs.** `tests/conftest.py:230` hardcodes the test DB as `epms_test` with no env override, and the session fixture `drop_all`s it. If another session is running an epms suite concurrently, both produce fabricated results. Confirm nothing else is running before starting.

**Pre-existing local failures that are NOT regressions:** suites needing approval-api / identity (`test_invoices.py`, `test_invoice_review.py`, `test_invoice_assign.py`) fail locally with `401 Invalid token` → `502` at PO approval submission. New tests in this plan are therefore built on `_make_issued_po` (see `tests/test_invoice_allocations.py:31`), which creates an issued PO directly and needs no approval service.

---

### Task 0: Give epms a test runner

epms has **no test runner at all** — no `test` script, no vitest, no jest (verified in `epms/package.json` on `2cbf817`). Every later task in this plan asserts money logic whose failure modes are invisible to `tsc`, so the runner comes first.

`packages/shell` is the in-repo precedent (`vitest ^2.1.8` + `vitest.config.ts` + `"test": "vitest run"`). Copy it, but **only the vitest dependency** — every test in this plan is a pure function, so `jsdom` and `@testing-library/*` are not needed. Add them later if component tests are ever wanted.

**Files:**
- Create: `epms/vitest.config.ts`
- Modify: `epms/package.json` (scripts + devDependencies)
- Modify: `epms/package-lock.json` (regenerated, never hand-edited)

**Interfaces:**
- Consumes: nothing
- Produces: `npm test` in `epms/` runs vitest once and exits

- [ ] **Step 1: Add the config**

Create `epms/vitest.config.ts`:

```typescript
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    globals: true,
    // 本仓库 epms 的测试目前全是纯函数(金额口径/差异计算),不碰 DOM。
    // 需要组件测试时再引 jsdom + @testing-library,照 packages/shell 的配置。
    environment: 'node',
  },
})
```

- [ ] **Step 2: Add the script and dependency**

In `epms/package.json`, add to `scripts`:

```json
    "test": "vitest run",
    "test:watch": "vitest"
```

Then install the dependency so the lock file is regenerated by npm rather than edited by hand:

```bash
cd /c/Project/uniops-payofficer/epms
npm install --save-dev vitest@^2.1.8
```

- [ ] **Step 3: Verify the runner starts and the lock file is consistent**

```bash
cd /c/Project/uniops-payofficer/epms
npx vitest run 2>&1 | tail -5      # expect "No test files found", exit cleanly — not a resolve error
npm ci --dry-run 2>&1 | tail -5    # must NOT report a package.json / package-lock mismatch
```

`npm ci --dry-run` matters: this repo already carries a known lock-file debt (a dependency declared in `package.json` but absent from `package-lock.json`, which makes `npm ci` fail outright). If this step surfaces that pre-existing problem, **report it — do not silently fix it**; it belongs to its own change, and quietly repairing it here would hide a production build risk inside an unrelated commit.

- [ ] **Step 4: Confirm the type gate still reports the same baseline**

```bash
cd /c/Project/uniops-payofficer/epms
npx tsc -p tsconfig.app.json 2>&1 | head -1
npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS"
```

Record this number — it is **the** baseline every later task compares against. Verify the first line is a real diagnostic (or empty), not an npm install hint; a hint means `node_modules` is incomplete and the count is a false negative.

- [ ] **Step 5: Commit**

```bash
cd /c/Project/uniops-payofficer
git add epms/vitest.config.ts epms/package.json epms/package-lock.json
git commit -m "chore(epms): add vitest so frontend money logic can be tested"
```

---

### Task 1: Extract `centsEqual` into a shared helper

`centsEqual` currently lives as a file-private function inside `InvoiceReceiptsPanel.tsx`, and its own comment records that this logic was already copy-relocated once from `MatchPanel`. Task 8 needs the identical comparison; extracting first prevents a third copy.

**Files:**
- Create: `epms/src/lib/money.ts`
- Create: `epms/src/lib/money.test.ts`
- Modify: `epms/src/components/invoices/InvoiceReceiptsPanel.tsx:10-18` (delete local copy), and its import block

**Interfaces:**
- Consumes: nothing
- Produces: `export function centsEqual(a: number, b: number): boolean` from `@/lib/money`

- [ ] **Step 1: Write the failing test**

Create `epms/src/lib/money.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { centsEqual } from './money'

describe('centsEqual', () => {
  it('treats exactly equal values as equal', () => {
    expect(centsEqual(100.25, 100.25)).toBe(true)
  })

  it('absorbs sub-cent float noise from summing decimal strings', () => {
    // 19.99 + 0.01 + 80.00 does not land exactly on 100 in IEEE754
    const summed = 19.99 + 0.01 + 80.0
    expect(summed === 100).toBe(false)
    expect(centsEqual(summed, 100)).toBe(true)
  })

  it('reports a genuine one-cent difference as unequal', () => {
    expect(centsEqual(100.0, 100.01)).toBe(false)
  })

  it('is symmetric', () => {
    expect(centsEqual(100.01, 100.0)).toBe(centsEqual(100.0, 100.01))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Project/uniops-payofficer/epms && npx vitest run src/lib/money.test.ts`
Expected: FAIL — cannot resolve `./money`. (Task 0 installed the runner; if this errors on vitest itself rather than on the missing module, Task 0 is incomplete.)

- [ ] **Step 3: Write minimal implementation**

Create `epms/src/lib/money.ts`:

```typescript
// Cent-rounded equality. Plain float subtraction of two Number()-coerced decimal
// strings can land a hair off zero (e.g. summing several receipt totals), which
// would otherwise make a genuinely-even match look like it has a variance.
//
// 提取自 InvoiceReceiptsPanel(它自己的注释记录了这段逻辑此前已从 MatchPanel
// 搬过一次)。凡是比较两笔金额是否相等,都用这个,不要写 === 0 / !== 0。
export function centsEqual(a: number, b: number): boolean {
  return Math.round(a * 100) === Math.round(b * 100)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Project/uniops-payofficer/epms && npx vitest run src/lib/money.test.ts`
Expected: PASS, 4 tests.

- [ ] **Step 5: Switch `InvoiceReceiptsPanel` to the shared helper**

In `epms/src/components/invoices/InvoiceReceiptsPanel.tsx`, delete the local `centsEqual` function (the `function centsEqual(...)` block at lines 16-18 together with its comment at lines 10-15) and add to the import block:

```typescript
import { centsEqual } from '@/lib/money'
```

Leave both call sites (lines 200 and 250) untouched — they now resolve to the import.

- [ ] **Step 6: Verify the frontend type gate**

```bash
cd /c/Project/uniops-payofficer/epms
npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS"
```

Expected: equal to the baseline recorded in Task 0 Step 4. **That baseline must have been measured on this branch — never copied from memory or another branch; it drifts with the code.**

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops-payofficer
git add epms/src/lib/money.ts epms/src/lib/money.test.ts epms/src/components/invoices/InvoiceReceiptsPanel.tsx
git commit -m "refactor(epms): extract centsEqual into a shared money helper"
```

---

### Task 2: Send the match-review task to the AP role pool

`invoices.py:535` sets `assigned_user_id=reviewer_id` alongside `assigned_role="ap_clerk"`. The notification recipient resolver (`app/services/notification.py:205-208`) short-circuits on a non-null `assigned_user_id` and mails that single person; the shared-mailbox branch (`notification.py:186-190`) is only consulted when `assigned_user_id is None`. Result: the AP team never hears about it. The notify call also fires before the request's commit, so the background reader (fresh session, lookup by id) can find nothing and return silently.

**Files:**
- Modify: `epms-api/app/api/v1/invoices.py:531-545`
- Test: `epms-api/tests/test_invoice_review_dispatch.py` (create)

**Interfaces:**
- Consumes: nothing
- Produces: `review_match` tasks with `assigned_user_id is None` and `assigned_role == "ap_clerk"`

- [ ] **Step 1: Write the failing test**

Create `epms-api/tests/test_invoice_review_dispatch.py`:

```python
"""review_match 任务必须落到 ap_clerk 角色池,而不是钉给某一个人。

钉给个人会让通知只发给派单者本人,并且绕过角色共享邮箱
(services/notification.py 只在 assigned_user_id 为空时才查共享邮箱)。
"""
import uuid

import pytest
from sqlalchemy import select

from tests.test_invoice_allocations import (
    INV_URL, _make_invoice, _make_issued_po, _make_vendor,
)


async def _review_task(invoice_id):
    import app.db.session as session_module
    from app.models.task import Task
    async with session_module.AsyncSessionLocal() as db:
        return (await db.execute(select(Task).where(
            Task.type == "review_match",
            Task.document_id == uuid.UUID(invoice_id),
            Task.is_completed.is_(False),
        ))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_review_task_goes_to_role_pool_not_one_person(admin_client):
    v = await _make_vendor(admin_client, "RVW1")
    po = await _make_issued_po(admin_client, v["id"], [
        {"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="RVW-0001", amount="900.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "900.00", "line_total": "900.00"}])

    delegate = await _delegate_matches_with_variance(admin_client, inv, po)
    assert delegate["status"] == "match_review", delegate

    task = await _review_task(inv["id"])
    assert task is not None, "match_review 状态必须伴随一个 review_match 任务"
    assert task.assigned_role == "ap_clerk"
    assert task.assigned_user_id is None, (
        "钉住个人会绕过 AP 共享邮箱、且只通知派单者一人")
```

`_delegate_matches_with_variance` must drive a **non-AP, non-uploader** caller holding an open `match_invoice` task through `POST /invoices/{id}/match` (that is what sets `require_review`). Model it on `tests/test_invoice_review.py:24-41`'s `_assigned_variance_match`, but build the PO with `_make_issued_po` so no approval service is needed. Write it as a helper at the top of this new file.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /c/Project/uniops-payofficer/epms-api
LP=$(docker exec uniops_postgres printenv POSTGRES_PASSWORD)
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD="$LP" \
  POSTGRES_DB=epms JWT_SECRET_KEY=test-secret \
  python -m pytest tests/test_invoice_review_dispatch.py -q
```

Expected: FAIL on `assert task.assigned_user_id is None` (it currently holds the delegator's id).

- [ ] **Step 3: Write minimal implementation**

In `epms-api/app/api/v1/invoices.py`, in the `review = Task(...)` construction, delete the `assigned_user_id=reviewer_id,` line so the task reads:

```python
            review = Task(
                type="review_match", priority="normal",
                document_type="invoice", document_id=inv.id,
                document_number=inv.internal_ref,
                # 角色池,不钉个人:钉住 assigned_user_id 会让 notification.py
                # 只发派单者一人,并跳过 AP 共享邮箱分支(它只在无指派人时才查)。
                assigned_role="ap_clerk",
                created_by=caller_id,
                title=f"Confirm invoice match — {inv.internal_ref}",
                description=review_description,
                vendor=inv.vendor_name, amount=inv.total_amount,
            )
            db.add(review)
            await db.flush()
            await db.refresh(review)
            # fire_and_forget_notify 的后台协程用**新 session** 按 id 读这条任务,
            # 所以必须先提交,否则它读不到、静默 return(与 assign_match 同因同修)。
            await db.commit()
            fire_and_forget_notify(review, db, extra_vars={"invoice_number": inv.internal_ref})
```

Note the title also changes here — that is Task 4's wording change, applied at the single place the string is constructed.

`reviewer_id` is still assigned earlier in the function and is still used by `match_review`'s reject path; leave that variable in place.

- [ ] **Step 4: Run test to verify it passes**

Same command as Step 2. Expected: PASS.

- [ ] **Step 5: Check for regressions in neighbouring suites**

```bash
cd /c/Project/uniops-payofficer/epms-api
LP=$(docker exec uniops_postgres printenv POSTGRES_PASSWORD)
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD="$LP" \
  POSTGRES_DB=epms JWT_SECRET_KEY=test-secret \
  python -m pytest tests/test_invoice_allocations.py tests/test_invoice_match_dispatch.py -q
```

Compare the **set** of failing test ids against the same command run on a clean checkout of `2cbf817`, not the totals. A changed count with an identical failure set is still a pass; an identical count with a different set is a regression.

- [ ] **Step 6: Commit**

```bash
cd /c/Project/uniops-payofficer
git add epms-api/app/api/v1/invoices.py epms-api/tests/test_invoice_review_dispatch.py
git commit -m "fix(epms): send match-review tasks to the AP pool and commit before notifying"
```

---

### Task 3: Create and close a task for invoices in `exception`

An invoice that lands outside tolerance gets `status="exception"` (`crud/invoice.py:1083-1088`) and **no Task at all**, so it is invisible in Task Inbox and unnotifiable. `resolve_exception` (`api/v1/invoices.py:1005`) must close it.

**Files:**
- Modify: `epms-api/app/api/v1/invoices.py` (match endpoint: create task; `resolve_exception`: close task)
- Modify: `epms-api/app/models/task.py:19` (type registry comment)
- Modify: `epms-api/app/services/notification.py:415-421` (`_infer_template` map)
- Modify: `epms-api/app/crud/config.py:226-231` (default email template)
- Test: `epms-api/tests/test_invoice_exception_task.py` (create)

**Interfaces:**
- Consumes: nothing
- Produces: task type `resolve_exception`; email template key `exception_resolution_request`

- [ ] **Step 1: Write the failing test**

Create `epms-api/tests/test_invoice_exception_task.py`:

```python
"""超容差发票必须产生一条可见、可通知的 AP 任务,并在解决后关闭。

在此之前 exception 状态不产生任何 Task —— AP 收不到提醒不是通知丢了,
是任务压根不存在。
"""
import uuid

import pytest
from sqlalchemy import select

from tests.test_invoice_allocations import (
    INV_URL, _make_invoice, _make_issued_po, _make_vendor,
)


async def _exception_task(invoice_id, *, open_only=True):
    import app.db.session as session_module
    from app.models.task import Task
    async with session_module.AsyncSessionLocal() as db:
        stmt = select(Task).where(
            Task.type == "resolve_exception",
            Task.document_id == uuid.UUID(invoice_id),
        )
        if open_only:
            stmt = stmt.where(Task.is_completed.is_(False))
        return (await db.execute(stmt)).scalar_one_or_none()


@pytest.mark.asyncio
async def test_exception_creates_ap_pool_task(admin_client):
    v = await _make_vendor(admin_client, "EXC1")
    po = await _make_issued_po(admin_client, v["id"], [
        {"description": "A", "qty": "1", "unit": "EA", "unit_price": "100.00"}])
    # 200 vs PO 100 = +100%,远超默认 5% 容差
    inv = await _make_invoice(admin_client, v["id"], number="EXC-0001", amount="200.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "200.00", "line_total": "200.00"}])
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
         "po_line_id": po["line_items"][0]["id"],
         "allocated_amount": "200.00", "allocated_tax": "0.00"}]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "exception", r.json()

    task = await _exception_task(inv["id"])
    assert task is not None, "exception 状态必须产生一条 AP 任务"
    assert task.assigned_role == "ap_clerk"
    assert task.assigned_user_id is None, "必须走角色池,才能命中 AP 共享邮箱"
    assert task.document_type == "invoice"


@pytest.mark.asyncio
async def test_resolving_exception_closes_the_task(admin_client):
    v = await _make_vendor(admin_client, "EXC2")
    po = await _make_issued_po(admin_client, v["id"], [
        {"description": "A", "qty": "1", "unit": "EA", "unit_price": "100.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="EXC-0002", amount="200.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "200.00", "line_total": "200.00"}])
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
         "po_line_id": po["line_items"][0]["id"],
         "allocated_amount": "200.00", "allocated_tax": "0.00"}]})
    assert await _exception_task(inv["id"]) is not None

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/exception",
                                json={"resolution": "accepted", "note": "approved by ops"})
    assert r.status_code == 200, r.text

    assert await _exception_task(inv["id"]) is None, "解决后不得留下开放任务"
    closed = await _exception_task(inv["id"], open_only=False)
    assert closed is not None and closed.is_completed is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /c/Project/uniops-payofficer/epms-api
LP=$(docker exec uniops_postgres printenv POSTGRES_PASSWORD)
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD="$LP" \
  POSTGRES_DB=epms JWT_SECRET_KEY=test-secret \
  python -m pytest tests/test_invoice_exception_task.py -q
```

Expected: both FAIL on `assert task is not None`.

- [ ] **Step 3: Register the new task type and its email template**

In `epms-api/app/models/task.py`, extend the registry comment on line 19:

```python
    #   INV: review_match | match_invoice | resolve_exception
```

In `epms-api/app/services/notification.py`, add to the `_infer_template` mapping (the dict ending at line 421):

```python
        "resolve_exception": "exception_resolution_request",
```

In `epms-api/app/crud/config.py`, add after the `match_review_request` entry (which ends at line 231):

```python
    "exception_resolution_request": _DEFAULT_EMAIL_TEMPLATE(
        "Invoice {invoice_number} is outside match tolerance",
        "Hi {recipient_name},\n\nInvoice <b>{invoice_number}</b> from {vendor} "
        "(CAD {amount}) could not be matched within tolerance. Please review the "
        "allocation and either resolve the exception or return the invoice.\n\n"
        "<a href=\"{link}\">Open Invoice</a>\n\n{company_name}",
    ),
```

- [ ] **Step 4: Create the task when a match lands in `exception`**

In `epms-api/app/api/v1/invoices.py`, inside `match_invoice`, add a branch alongside the existing `if result.status == "match_review"` block:

```python
        if result.status == "exception":
            exc_task = Task(
                type="resolve_exception", priority="normal",
                document_type="invoice", document_id=inv.id,
                document_number=inv.internal_ref,
                # 角色池(无指派人)——与 review_match 同理,这样才能命中共享邮箱。
                assigned_role="ap_clerk",
                created_by=caller_id,
                title=f"Resolve match exception — {inv.internal_ref}",
                description=(
                    f"Invoice {inv.internal_ref} could not be matched within tolerance: "
                    f"{result.exception_reason} Please resolve the exception or return "
                    f"the invoice to the supplier."
                ),
                vendor=inv.vendor_name, amount=inv.total_amount,
            )
            db.add(exc_task)
            await db.flush()
            await db.refresh(exc_task)
            await db.commit()   # 见 Task 2:后台通知协程读的是新 session
            fire_and_forget_notify(exc_task, db, extra_vars={"invoice_number": inv.internal_ref})
```

Guard against duplicates: a re-match of an invoice that is already in `exception` must not stack a second open task. Before constructing `exc_task`, look for an existing open one and skip creation if found:

```python
            existing_exc = (await db.execute(select(Task).where(
                Task.type == "resolve_exception", Task.document_type == "invoice",
                Task.document_id == inv.id, Task.is_completed.is_(False),
            ))).scalar_one_or_none()
```

and wrap the creation in `if existing_exc is None:`.

- [ ] **Step 5: Close the task in `resolve_exception`**

In `epms-api/app/api/v1/invoices.py`'s `resolve_exception` endpoint, after `invoice_crud.resolve_exception(...)` succeeds and before the `finance_sync` call, close any open task:

```python
        exc_task = (await db.execute(select(Task).where(
            Task.type == "resolve_exception", Task.document_type == "invoice",
            Task.document_id == inv.id, Task.is_completed.is_(False),
        ))).scalar_one_or_none()
        if exc_task is not None:
            exc_task.is_completed = True
            exc_task.completed_at = datetime.now(timezone.utc)
            exc_task.completed_by = uuid.UUID(user["sub"])
```

`Task`, `select`, `datetime`, `timezone` and `uuid` are already imported in this module.

Note the invoice may also leave `exception` by being re-matched successfully. Add the same close block to the `match_invoice` endpoint for the case `result.status != "exception"` — otherwise a successful re-match leaves a stale open task, which is the exact orphan-task class that has burned this codebase before.

- [ ] **Step 6: Run tests to verify they pass**

Same command as Step 2. Expected: 2 passed.

- [ ] **Step 7: Register the task type in the frontend**

In `epms/src/lib/taskTypes.ts`, add `'resolve_exception'` to the `ALL_TASK_TYPES` array (after `'match_invoice'`) and add the label:

```typescript
  resolve_exception: 'Resolve Match Exception',
```

- [ ] **Step 8: Verify the frontend type gate**

```bash
cd /c/Project/uniops-payofficer/epms
npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS"
```

Expected: unchanged from the Task 0 baseline.

- [ ] **Step 9: Commit**

```bash
cd /c/Project/uniops-payofficer
git add epms-api/app/api/v1/invoices.py epms-api/app/models/task.py \
        epms-api/app/services/notification.py epms-api/app/crud/config.py \
        epms-api/tests/test_invoice_exception_task.py epms/src/lib/taskTypes.ts
git commit -m "feat(epms): raise and close an AP task for invoices outside match tolerance"
```

---

### Task 4: Reword the review task and dialog as a confirmation, not an approval

AP reads "Review match variance … approve or reject" as being asked to approve a payment difference. It is not — payment is authorised on the PA approval chain. The task title was already changed in Task 2; this task covers the descriptions and the dialog.

**Files:**
- Modify: `epms-api/app/api/v1/invoices.py` (the `review_description` branches, lines ~496-530)
- Modify: `epms/src/pages/invoices/InvoiceDetailPage.tsx` (the review dialog copy, around line 463)
- Modify: `epms-api/app/crud/config.py` (the `match_review_request` template body)

**Interfaces:**
- Consumes: nothing
- Produces: nothing

- [ ] **Step 1: Reword the PO-route description**

In `epms-api/app/api/v1/invoices.py`, replace the `else:` branch's `review_description`:

```python
            else:
                review_description = (
                    f"Invoice {inv.internal_ref} was matched to its purchase order by a "
                    f"delegate, with a variance of {result.variance}. Please confirm the "
                    f"invoice is linked to the correct PO and goods receipt. Approval of "
                    f"the payment amount happens later, on the Payment Application "
                    f"approval chain."
                )
```

Apply the same closing sentence to the two agreement-route branches, replacing their trailing "Please review and approve or reject." with "Please confirm the linkage is correct. Approval of the payment amount happens later, on the Payment Application approval chain."

- [ ] **Step 2: Reword the email template**

In `epms-api/app/crud/config.py`, replace the `match_review_request` body:

```python
    "match_review_request": _DEFAULT_EMAIL_TEMPLATE(
        "Confirm invoice match — {invoice_number}",
        "Hi {recipient_name},\n\nA delegate has matched invoice <b>{invoice_number}</b> "
        "on your behalf. Please confirm it is linked to the correct purchase order and "
        "goods receipt.\n\nThis is a confirmation of the linkage only — approval of the "
        "payment amount happens later, on the Payment Application approval chain.\n\n"
        "<a href=\"{link}\">Confirm Match</a>\n\n{company_name}",
    ),
```

- [ ] **Step 3: Reword the dialog**

In `epms/src/pages/invoices/InvoiceDetailPage.tsx`, find the review banner near line 463 that currently reads "This invoice was matched with a variance of …" and rewrite it to state what confirming means and where payment is approved. Keep it English, keep the existing amount interpolation, and add a muted second line:

```tsx
Confirming records that this invoice is linked to the correct PO and goods
receipt. It does not approve the payment amount — that happens on the Payment
Application approval chain.
```

- [ ] **Step 4: Verify the frontend type gate**

```bash
cd /c/Project/uniops-payofficer/epms
npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS"
```

Expected: unchanged from the Task 0 baseline.

- [ ] **Step 5: Confirm no test asserted the old copy**

```bash
cd /c/Project/uniops-payofficer
grep -rn "approve or reject" epms-api/tests/ epms/src/ || echo "no stale copy assertions"
```

If any test asserts the old wording, update the assertion to the new wording in the same commit.

- [ ] **Step 6: Commit**

```bash
cd /c/Project/uniops-payofficer
git add epms-api/app/api/v1/invoices.py epms-api/app/crud/config.py \
        epms/src/pages/invoices/InvoiceDetailPage.tsx
git commit -m "docs(epms): reword match review as a linkage confirmation, not a payment approval"
```

---

### Task 5: Expose the claimed agreement receipts on `InvoiceResponse`

The existing receipts route (`GET /invoices/{id}/agreements/{aid}/receipts`) is gated by `_require_invoice_match_access` — AP, the uploader, or a match-task holder. PA approvers (GM, Finance Manager, department managers) are not in that set and get 403, and the route only searches `candidates_for_vendor`, so a closed agreement 404s. The PA panel therefore cannot use it.

**Files:**
- Modify: `epms-api/app/schemas/invoice.py` (add `ClaimedReceipt` + field on `InvoiceResponse`)
- Modify: `epms-api/app/crud/invoice.py` (populate it)
- Test: `epms-api/tests/test_invoice_claimed_receipts.py` (create)

**Interfaces:**
- Consumes: nothing
- Produces: `InvoiceResponse.claimed_receipts: list[ClaimedReceipt] | None` with fields `id: UUID`, `receipt_ref: str | None`, `receipt_date: date`, `receipt_type: str`, `total_amount: Decimal | None`, `vendor_name: str | None`

- [ ] **Step 1: Write the failing test**

Create `epms-api/tests/test_invoice_claimed_receipts.py` asserting that an invoice matched to a house-account agreement with two claimed receipts returns both in `claimed_receipts`, that a receipt with no amount comes back with `total_amount is None` (not `0`), and that an invoice with no claimed receipts returns `None` or `[]` consistently. Build the agreement and receipts with the helpers in `tests/test_invoice_receipts.py` — read that file first and reuse its fixtures rather than writing new ones.

The critical assertion:

```python
    amounts = {r["receipt_ref"]: r["total_amount"] for r in body["claimed_receipts"]}
    assert amounts["SLIP-1"] == "120.00"
    assert amounts["DN-1"] is None, (
        "送货单没有金额,必须原样返回 None —— 折成 0 会让前端把它算进汇总")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /c/Project/uniops-payofficer/epms-api
LP=$(docker exec uniops_postgres printenv POSTGRES_PASSWORD)
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD="$LP" \
  POSTGRES_DB=epms JWT_SECRET_KEY=test-secret \
  python -m pytest tests/test_invoice_claimed_receipts.py -q
```

Expected: FAIL — `claimed_receipts` absent from the response.

- [ ] **Step 3: Add the schema**

In `epms-api/app/schemas/invoice.py`, above `class InvoiceResponse`:

```python
class ClaimedReceipt(BaseModel):
    """协议凭证的只读投影,随发票读权限(view_invoice 矩阵)返回。

    存在的理由:GET /invoices/{id}/agreements/{aid}/receipts 的门禁是
    _require_invoice_match_access(AP / 上传人 / 持 match 任务者),PA 审批人
    全部 403;而且它只在 candidates_for_vendor 里找,已关闭的协议直接 404。
    PA 差异面板的读者正是那批审批人,所以数据必须走发票自己的读权限下发。

    total_amount 可空且**必须保持可空** —— delivery / service 两类凭证本就
    没有金额(ag09 起放开),折成 0 会让前端把它算进汇总,凭空造出差异。
    """
    id: uuid.UUID
    receipt_ref: str | None = None
    receipt_date: date
    receipt_type: str
    total_amount: Decimal | None = None
    vendor_name: str | None = None
```

Then add to `InvoiceResponse`, next to the existing `receipt_ids` field:

```python
    claimed_receipts: list[ClaimedReceipt] | None = None
```

- [ ] **Step 4: Populate it**

In `epms-api/app/crud/invoice.py`, add a helper that batch-loads receipts for a set of invoices and attaches them to the ORM instances, mirroring how `_attach_match_assignees` decorates invoices in `api/v1/invoices.py`. Load with a single `WHERE AgreementReceipt.id IN (...)` query across all invoices being serialised — **never one query per invoice**.

Call it from the same places that already decorate invoice responses (detail and list). Read `api/v1/invoices.py`'s existing `_attach_match_assignees` call sites and add the new attachment at each, so list and detail stay consistent.

- [ ] **Step 5: Run test to verify it passes**

Same command as Step 2. Expected: PASS.

- [ ] **Step 6: Verify there is no N+1**

Add a test that serialises a list of 5 house-account invoices and asserts the receipt query executes once. If the suite has no query-counting fixture, instead assert by reading the code that a single `IN` query is used, and record that in the commit message — do not claim a performance property you did not measure.

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops-payofficer
git add epms-api/app/schemas/invoice.py epms-api/app/crud/invoice.py \
        epms-api/app/api/v1/invoices.py epms-api/tests/test_invoice_claimed_receipts.py
git commit -m "feat(epms): expose claimed agreement receipts on the invoice response"
```

---

### Task 6: Fix the PA Detail invoice fetch for agreement PAs and long PO histories

`PaDetailPage.tsx:228` skips the invoice fetch entirely when `pa.po_id` is null, so an agreement PA always renders "No invoices linked". It also uses the default `page_size=20` and then filters client-side by `pa.invoice_ids`, so a busy PO silently drops invoices.

**Files:**
- Modify: `epms/src/pages/pa/PaDetailPage.tsx:226-231`

**Interfaces:**
- Consumes: `useInvoices(filters?: InvoiceFilters, enabled = true)` from `@/hooks/useInvoices`
- Produces: `linkedInvoices` now populated for agreement PAs

- [ ] **Step 1: Replace the fetch**

```tsx
  // PO 路由按 po_id 取,协议路由按 agreement_id 取。原先只判 pa.po_id,
  // 协议 PA(po_id 为 null)整个跳过 fetch,Linked Documents 永远显示
  // "No invoices linked"。agreement_id 过滤后端早就支持(DocumentChainTree 已在用)。
  //
  // page_size 显式给到 200:默认 20 会在繁忙 PO 上截断,而下面还要按
  // pa.invoice_ids 客户端过滤 —— 被截掉的发票就这样静默消失。
  const invoiceFilters = pa?.invoice_ids.length
    ? (pa.po_id
        ? { po_id: pa.po_id, page_size: 200 }
        : pa.agreement_id
          ? { agreement_id: pa.agreement_id, page_size: 200 }
          : undefined)
    : undefined
  const { data: invoicesData } = useInvoices(invoiceFilters, !!invoiceFilters)
  const linkedInvoices = (invoicesData?.items ?? []).filter((inv) => pa?.invoice_ids.includes(inv.id))
```

- [ ] **Step 2: Confirm `InvoiceFilters` accepts `page_size` and `agreement_id`**

```bash
cd /c/Project/uniops-payofficer
grep -n "InvoiceFilters" -A 12 epms/src/hooks/useInvoices.ts
```

If either key is missing from the type, add it — the backend already accepts both (`api/v1/invoices.py:227-237`).

- [ ] **Step 3: Verify the frontend type gate**

```bash
cd /c/Project/uniops-payofficer/epms
npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS"
```

Expected: unchanged from the Task 0 baseline.

- [ ] **Step 4: Verify against a running app**

This is a reachability fix — a type check does not prove a user can see anything. Open a PO-route PA and an agreement-route PA in the dev stack and confirm both list their invoices. Recall that dev containers mount the **main checkout**, not this worktree; to exercise this branch, either check it out in the integration checkout or mount this worktree per the documented override.

- [ ] **Step 5: Commit**

```bash
cd /c/Project/uniops-payofficer
git add epms/src/pages/pa/PaDetailPage.tsx
git commit -m "fix(epms): load linked invoices for agreement PAs and stop truncating PO histories"
```

---

### Task 7: PA Detail variance panel — PO route

**Files:**
- Create: `epms/src/components/invoices/InvoiceMatchVariancePanel.tsx`
- Create: `epms/src/lib/matchVariance.ts`
- Create: `epms/src/lib/matchVariance.test.ts`
- Modify: `epms/src/pages/pa/PaDetailPage.tsx` (render it under Linked Documents)

**Interfaces:**
- Consumes: `centsEqual` from `@/lib/money`; `Invoice`, `InvoiceAllocation` from `@/services/invoices`
- Produces:
  - `export type MatchMode = 'by-line' | 'by-amount'`
  - `export function matchMode(allocations: InvoiceAllocation[]): MatchMode`
  - `export interface LineComparison { allocationId: string; invoiceLineDescription: string; invoiceQty: number | null; invoiceUnitPrice: number | null; invoiceAmount: number; poLineDescription: string | null; poQty: number | null; poUnitPrice: number | null; poAmount: number | null; variance: number }`
  - `export function buildLineComparisons(invoice: Invoice, poLines: ApiPoLineItem[]): LineComparison[]`
  - `export function InvoiceMatchVariancePanel(props: { invoice: Invoice; poLines: ApiPoLineItem[] | undefined; tolerancePct: number }): JSX.Element | null`

- [ ] **Step 1: Write the failing test for the pure functions**

Create `epms/src/lib/matchVariance.test.ts` covering:
- `matchMode` returns `'by-line'` when every allocation has a non-null `po_line_id`
- `matchMode` returns `'by-amount'` when allocations have `po_line_id === null`
- `buildLineComparisons` joins invoice line → allocation → PO line and computes per-row variance
- `buildLineComparisons` handles a `Decimal`-as-string payload: allocations arriving as `"900.00"` must be coerced with `Number()` before arithmetic
- a PO line id present on the allocation but absent from `poLines` yields `poAmount: null` rather than throwing

Write the assertions with literal fixture objects in the test file; do not import production fixtures.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Project/uniops-payofficer/epms && npx vitest run src/lib/matchVariance.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the pure functions**

Create `epms/src/lib/matchVariance.ts` implementing `matchMode` and `buildLineComparisons` exactly as specified in the Interfaces block. Coerce every `Decimal` field through `Number()` on entry. Do not render anything here.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Project/uniops-payofficer/epms && npx vitest run src/lib/matchVariance.test.ts`
Expected: PASS.

- [ ] **Step 5: Build the panel component**

Create `epms/src/components/invoices/InvoiceMatchVariancePanel.tsx`:

- Collapsed header row: `{invoice.internal_ref} · Total variance {formatAmount(variance)} ({variancePct}%)`, click to expand.
- Colour: green when `centsEqual(variance, 0)`; amber when within `tolerancePct`; red when outside it.
- Expanded, `by-line`: a three-column table — Invoice Line (description / qty / unit price / amount), PO Line (description / qty / unit price / amount), Variance.
- Expanded, `by-amount`: list the invoice line items and the amount allocated to each PO. **Render no line-to-line correspondence** — it does not exist in this mode, and drawing one fabricates data.
- Wide tables must scroll inside their own container, not push the page sideways.
- Reuse the existing 3-way-match / PO-allocations presentation on `InvoiceDetailPage.tsx` (around lines 1070-1190) rather than inventing a second visual language.

- [ ] **Step 6: Mount it on PA Detail**

In `epms/src/pages/pa/PaDetailPage.tsx`, render one panel per entry of `linkedInvoices` directly below the Linked Documents card, passing `poLines={po?.line_items}` and the tolerance from the config hook used by `InvoiceDetailPage` (grep `matchTolerancePct` there for the exact hook).

Render only when `pa.po_id` is set; the agreement route is Task 8.

- [ ] **Step 7: Verify the frontend type gate**

```bash
cd /c/Project/uniops-payofficer/epms
npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS"
```

Expected: unchanged from the Task 0 baseline.

- [ ] **Step 8: Verify against a running app**

Open a PA whose invoice was matched by line and one matched by total value; confirm the two expanded views differ as specified and that the by-amount view shows no PO-line column.

- [ ] **Step 9: Commit**

```bash
cd /c/Project/uniops-payofficer
git add epms/src/lib/matchVariance.ts epms/src/lib/matchVariance.test.ts \
        epms/src/components/invoices/InvoiceMatchVariancePanel.tsx \
        epms/src/pages/pa/PaDetailPage.tsx
git commit -m "feat(epms): show invoice-to-PO variance with line detail on PA Detail"
```

---

### Task 8: PA Detail variance panel — house-account receipts

**Files:**
- Modify: `epms/src/lib/matchVariance.ts` (add receipt summary)
- Modify: `epms/src/lib/matchVariance.test.ts`
- Modify: `epms/src/components/invoices/InvoiceMatchVariancePanel.tsx`
- Modify: `epms/src/pages/pa/PaDetailPage.tsx`

**Interfaces:**
- Consumes: `InvoiceResponse.claimed_receipts` from Task 5; `centsEqual` from `@/lib/money`
- Produces: `export function receiptSummary(invoice: Invoice): { pricedCount: number; receiptTotal: number; variance: number; hasVariance: boolean }`

- [ ] **Step 1: Write the failing test**

Add to `epms/src/lib/matchVariance.test.ts`:

```typescript
describe('receiptSummary', () => {
  const inv = (total: string, receipts: Array<string | null>) => ({
    total_amount: total,
    claimed_receipts: receipts.map((total_amount, i) => ({
      id: `r${i}`, receipt_ref: `R${i}`, receipt_date: '2026-08-01',
      receipt_type: total_amount === null ? 'delivery' : 'counter_slip',
      total_amount, vendor_name: null,
    })),
  }) as never

  it('excludes receipts that carry no amount', () => {
    const s = receiptSummary(inv('120.00', ['120.00', null]))
    expect(s.pricedCount).toBe(1)
    expect(s.receiptTotal).toBe(120)
    expect(s.hasVariance).toBe(false)
  })

  it('reports zero variance when no priced receipt is claimed', () => {
    const s = receiptSummary(inv('120.00', [null, null]))
    expect(s.hasVariance).toBe(false)
    // 关键:绝不能得出「差异 = 整张发票 120」这种误报
    expect(s.variance).toBe(0)
  })

  it('compares against the tax-INCLUSIVE invoice total', () => {
    // total_amount 是含税总额 —— 与 PO 分摊路线的税前口径**相反**,这是对的
    const s = receiptSummary(inv('113.00', ['113.00']))
    expect(s.hasVariance).toBe(false)
  })

  it('absorbs sub-cent float noise', () => {
    const s = receiptSummary(inv('100.00', ['19.99', '0.01', '80.00']))
    expect(s.hasVariance).toBe(false)
  })

  it('flags a genuine difference', () => {
    const s = receiptSummary(inv('120.00', ['100.00']))
    expect(s.hasVariance).toBe(true)
    expect(s.variance).toBeCloseTo(-20, 2)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Project/uniops-payofficer/epms && npx vitest run src/lib/matchVariance.test.ts`
Expected: FAIL — `receiptSummary` is not exported.

- [ ] **Step 3: Implement `receiptSummary`**

```typescript
// 口径必须与 InvoiceReceiptsPanel 一致(该文件是既有事实来源):
//   ① 比的是含税 total_amount,不是税前 amount —— 柜台小票含税。
//      与 PO 分摊路线口径相反,这是正确的,不是笔误。
//   ② 只有携带金额的凭证参与比较;delivery / service 没有金额。
//   ③ 一张计价凭证都没有 → 零差异。否则会得出「差异 = 整张发票」的纯误报。
//   ④ 用 centsEqual,不用 !== 0。
export function receiptSummary(invoice: Invoice) {
  const priced = (invoice.claimed_receipts ?? []).filter(
    (r) => r.total_amount !== null && r.total_amount !== undefined)
  const receiptTotal = priced.reduce((sum, r) => sum + Number(r.total_amount), 0)
  const invoiceTotal = Number(invoice.total_amount)
  const hasPriced = priced.length > 0
  return {
    pricedCount: priced.length,
    receiptTotal,
    variance: hasPriced ? receiptTotal - invoiceTotal : 0,
    hasVariance: hasPriced && !centsEqual(receiptTotal, invoiceTotal),
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Project/uniops-payofficer/epms && npx vitest run src/lib/matchVariance.test.ts`
Expected: PASS.

- [ ] **Step 5: Render the house-account branch**

In `InvoiceMatchVariancePanel.tsx`, when the invoice's `agreement_type === 'house_account'`:

- Render **nothing at all** unless `receiptSummary(invoice).hasVariance` is true.
- Collapsed header: `{invoice.internal_ref} · Receipts differ from invoice by {formatAmount(variance)}`.
- Expanded: a table of the claimed receipts — reference, date, type, amount — with amount-less receipts shown as "no amount" and visibly excluded from the total.

For `agreement_type` of `recurring` or `milestone`, render nothing.

- [ ] **Step 6: Mount the agreement branch on PA Detail**

Extend the render condition in `PaDetailPage.tsx` so panels also render when `pa.agreement_id` is set. The panel itself decides whether it has anything to show.

- [ ] **Step 7: Verify the frontend type gate**

```bash
cd /c/Project/uniops-payofficer/epms
npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS"
```

Expected: unchanged from the Task 0 baseline.

- [ ] **Step 8: Verify against a running app**

Confirm three cases on real data: a house-account PA whose receipts match exactly (panel absent), one with a real difference (panel present, correct amount), and one whose only evidence is a delivery note with no amount (panel absent — **not** a full-invoice variance).

- [ ] **Step 9: Commit**

```bash
cd /c/Project/uniops-payofficer
git add epms/src/lib/matchVariance.ts epms/src/lib/matchVariance.test.ts \
        epms/src/components/invoices/InvoiceMatchVariancePanel.tsx \
        epms/src/pages/pa/PaDetailPage.tsx
git commit -m "feat(epms): surface house-account receipt differences on PA Detail"
```

---

## Phase 1 exit criteria

- [ ] `epms-api` suites run and the **failure set** is compared against `2cbf817`, not the totals
- [ ] `npx tsc -p tsconfig.app.json` error count equals the baseline recorded in Task 0
- [ ] All new vitest files pass
- [ ] Reachability confirmed in a running app for Tasks 6, 7 and 8 — a type check is not evidence that a user can see the panel
- [ ] No `.env` or credential file committed; `git status` clean
