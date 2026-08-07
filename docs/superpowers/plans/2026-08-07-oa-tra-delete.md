# Delete Unapproved Travel Applications — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the applicant (or a system admin) hard-delete a Travel Application that has not been approved yet, from both the Travel Applications list and the detail page.

**Architecture:** One new `DELETE /api/v1/expenses/{claim_id}` endpoint in expense-api, restricted to `claim_type == "TRA"` in `draft`/`returned`/`submitted`/`in_review`. Deletion reuses the cascade Data Maintenance already uses — purge `tasks` + `approval_events` by `document_id` (they have no FK to `expense_claims`), then `db.delete(claim)` and let FK CASCADE take the children. A pure `_can_delete_claim()` helper drives the endpoint gate, the `/permissions` response, and a `can_delete` flag on each list row, so the frontend carries no permission logic.

**Tech Stack:** FastAPI + SQLAlchemy 2 async (expense-api), React 19 + TanStack Query + Tailwind (oa), pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-07-oa-tra-delete-design.md`

## Global Constraints

- Branch `feature/oa-tra-delete`, worktree `c:/Project/uniops-tra-delete`, base `origin/main` = d57578d. Never commit on `main`.
- **No migration.** No schema change is required by this feature.
- UI copy is English only; code comments may be Chinese ([UI 文案全英文](feedback_uniops_ui_english_only)).
- Backend tests run in the `uniops_expense_api` container against `/tmp/wt`, never on the host (host `.env` points at the production DB). Git Bash mangles container paths — every `docker exec -w` needs `MSYS_NO_PATHCONV=1`, and `docker cp` targets need a doubled slash: `uniops_expense_api://tmp/wt/...`.
- Baseline for the backend suite on `origin/main` in that container: **146 passed, 0 failed**. Compare against it; never read "no output" as passing.
- Frontend gate: `cd oa && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` → **0 errors**. Requires `npm ci` in the worktree first, otherwise tsc prints an install prompt and a grep for errors returns a false negative.
- Deletable statuses, used verbatim everywhere: `("draft", "returned", "submitted", "in_review")`.

---

## Container test harness (do this once, before Task 1)

- [ ] **Step 1: Stage the worktree code inside the container**

```bash
cd c:/Project/uniops-tra-delete
docker exec uniops_expense_api sh -c 'rm -rf /tmp/wt && mkdir -p /tmp/wt && cp -r /app/alembic /app/alembic.ini /app/pyproject.toml /tmp/wt/'
docker cp expense-api/app uniops_expense_api://tmp/wt/app
docker cp expense-api/tests uniops_expense_api://tmp/wt/tests
```

- [ ] **Step 2: Confirm the baseline before touching anything**

```bash
MSYS_NO_PATHCONV=1 docker exec -w /tmp/wt uniops_expense_api python -m pytest -q 2>&1 | tail -3
```

Expected: `146 passed`. If the number differs, stop and reconcile before writing code — a drifting baseline makes every later comparison meaningless.

After each backend edit, re-sync only the changed files, e.g.:

```bash
docker cp expense-api/app/crud/expense.py uniops_expense_api://tmp/wt/app/crud/expense.py
```

---

### Task 1: Shared delete path + permission helper

**Files:**
- Modify: `expense-api/app/crud/expense.py` (add `delete_claim`)
- Modify: `expense-api/app/admin/registry.py:59-62` (`_claim_delete` delegates to it)
- Test: `expense-api/tests/test_tra_delete.py` (create)

**Interfaces:**
- Produces: `async def delete_claim(db: AsyncSession, claim: ExpenseClaim) -> dict[str, int]` — purges shared refs, deletes the claim, returns `{"expense_claims": 1, "tasks": N, "approval_events": M}`. Caller commits.
- Produces: `def can_delete_claim(claim: ExpenseClaim, user_id: uuid.UUID, role: str) -> bool` in `app/api/v1/expenses.py` — pure, no queries.

- [ ] **Step 1: Write the failing test**

Create `expense-api/tests/test_tra_delete.py`:

```python
"""DELETE /api/v1/expenses/{id} — hard-delete an unapproved Travel Application.

TRA shares expense_claims with EXP/MIL/TRV but is a pre-trip authorization, not
a reimbursement. An application raised by mistake had no way out: recall/cancel
exist in the approval engine but are not exposed in OA, so it sat in the list
and kept an approve task alive in approvers' inboxes.

`tasks` and `approval_events` are approval-api's polymorphic tables keyed by
document_id with NO foreign key to expense_claims — deleting the claim alone
leaves orphaned approve tasks. Data Maintenance already solved this
(app/admin/registry.py::_claim_delete); the delete path is shared rather than
duplicated.

Fixture pattern mirrors test_travel_application_list.py.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import func, select

import app.db.base as db_module
from app.core.config import settings
from app.main import create_app
from app.models.approval_event_mirror import ApprovalEventMirror
from app.models.expense import ExpenseClaim, ExpenseTraveler
from app.models.task_mirror import TaskMirror


def _client_for(role: str, user_id: str) -> AsyncClient:
    token = jwt.encode(
        {"sub": user_id, "role": role, "exp": datetime.utcnow() + timedelta(hours=8)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _seed_tra(owner_id: str, status: str = "draft", *, travelers: bool = False) -> str:
    async with db_module.AsyncSessionLocal() as db:
        tra = ExpenseClaim(
            claim_number=f"TRA-TEST-{uuid.uuid4().hex[:8]}", claim_type="TRA",
            employee_id=uuid.UUID(owner_id), employee_name="Owner",
            department_name="Ops", submission_date=date(2026, 8, 3),
            status=status, created_by=uuid.UUID(owner_id),
        )
        db.add(tra)
        await db.flush()
        if travelers:
            db.add(ExpenseTraveler(claim_id=tra.id, user_id=uuid.UUID(owner_id), user_name="Owner"))
        await db.commit()
        return str(tra.id)


async def _count(model, claim_id: str) -> int:
    async with db_module.AsyncSessionLocal() as db:
        return int((await db.execute(
            select(func.count()).select_from(model)
            .where(model.document_id == uuid.UUID(claim_id))
        )).scalar_one())


async def _exists(claim_id: str) -> bool:
    async with db_module.AsyncSessionLocal() as db:
        return (await db.get(ExpenseClaim, uuid.UUID(claim_id))) is not None


@pytest.mark.asyncio
async def test_delete_claim_purges_shared_refs_and_children():
    """crud.delete_claim removes the claim, its FK children, and the polymorphic
    tasks/approval_events rows that have no FK to expense_claims."""
    from app.crud import expense as expense_crud

    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "submitted", travelers=True)

    async with db_module.AsyncSessionLocal() as db:
        # TaskMirror.id has no default (the table is written by approval-api) —
        # it must be supplied explicitly. Columns verified against
        # app/models/task_mirror.py: `type`/`is_completed`, not task_type/status.
        db.add(TaskMirror(
            id=uuid.uuid4(), document_type="tra", document_id=uuid.UUID(claim_id),
            type="approve_tra", assigned_role="dept_manager", is_completed=False,
        ))
        # document_number and actor_role are NOT NULL on approval_events.
        db.add(ApprovalEventMirror(
            document_type="tra", document_id=uuid.UUID(claim_id),
            document_number="TRA-TEST", step_idx=0, action="submit",
            actor_id=uuid.UUID(owner), actor_role="employee",
        ))
        await db.commit()

    assert await _count(TaskMirror, claim_id) == 1
    assert await _count(ApprovalEventMirror, claim_id) == 1

    async with db_module.AsyncSessionLocal() as db:
        claim = await db.get(ExpenseClaim, uuid.UUID(claim_id))
        summary = await expense_crud.delete_claim(db, claim)
        await db.commit()

    assert summary == {"expense_claims": 1, "tasks": 1, "approval_events": 1}
    assert not await _exists(claim_id)
    assert await _count(TaskMirror, claim_id) == 0
    assert await _count(ApprovalEventMirror, claim_id) == 0

    async with db_module.AsyncSessionLocal() as db:
        travelers = int((await db.execute(
            select(func.count()).select_from(ExpenseTraveler)
            .where(ExpenseTraveler.claim_id == uuid.UUID(claim_id))
        )).scalar_one())
    assert travelers == 0, "expense_travelers should cascade via FK"
```

Column names above were read off `app/models/task_mirror.py` and
`app/models/approval_event_mirror.py`, not guessed — mirror models must match
the physical tables ([镜像模型忠于物理表](feedback_uniops_mirror_models_match_reality)).
`ExpenseTraveler` takes `claim_id` / `user_id` / `user_name` and inherits a
defaulted UUID primary key.

- [ ] **Step 2: Run it and watch it fail**

```bash
cd c:/Project/uniops-tra-delete
docker cp expense-api/tests uniops_expense_api://tmp/wt/tests
MSYS_NO_PATHCONV=1 docker exec -w /tmp/wt uniops_expense_api python -m pytest tests/test_tra_delete.py -q 2>&1 | tail -5
```

Expected: FAIL with `AttributeError: module 'app.crud.expense' has no attribute 'delete_claim'`.

- [ ] **Step 3: Implement `delete_claim`**

In `expense-api/app/crud/expense.py`, after `list_claims`:

```python
async def delete_claim(db: AsyncSession, claim: ExpenseClaim) -> dict[str, int]:
    """Hard-delete a claim and everything hanging off it. The CALLER commits.

    `tasks` and `approval_events` are approval-api's polymorphic tables keyed by
    document_id with no FK to expense_claims — without an explicit purge the
    claim vanishes while its approve task lives on in approvers' inboxes. Line
    items, trip items, travelers, attachments and expense_approval_events go via
    ON DELETE CASCADE.
    """
    from app.admin.cascade import purge_shared_refs

    refs = await purge_shared_refs(db, claim.id)
    await db.delete(claim)
    await db.flush()
    return {"expense_claims": 1, **refs}
```

The import is function-local to keep `app.crud` free of an import cycle with `app.admin`.

- [ ] **Step 4: Point Data Maintenance at the same code**

In `expense-api/app/admin/registry.py`, replace the body of `_claim_delete`:

```python
async def _claim_delete(db: AsyncSession, claim) -> dict[str, int]:
    from app.crud.expense import delete_claim
    return await delete_claim(db, claim)
```

Leave `_claim_preview` alone — preview counts, it does not mutate.

- [ ] **Step 5: Run the new test plus the admin suite**

```bash
cd c:/Project/uniops-tra-delete
docker cp expense-api/app uniops_expense_api://tmp/wt/app
MSYS_NO_PATHCONV=1 docker exec -w /tmp/wt uniops_expense_api python -m pytest tests/test_tra_delete.py tests/test_admin.py -q 2>&1 | tail -5
```

Expected: all pass. `test_admin.py` proves the Data Maintenance delete still behaves after being rerouted.

- [ ] **Step 6: Commit**

```bash
git add expense-api/app/crud/expense.py expense-api/app/admin/registry.py expense-api/tests/test_tra_delete.py
git commit -m "feat(oa): shared hard-delete path for expense claims

Extracts the Data Maintenance cascade into crud.delete_claim so the
upcoming TRA delete endpoint and the admin registry share one
implementation. Purges tasks + approval_events by document_id (no FK to
expense_claims) before deleting the row."
```

---

### Task 2: `DELETE /api/v1/expenses/{claim_id}`

**Files:**
- Modify: `expense-api/app/api/v1/expenses.py` (add `can_delete_claim` + the endpoint)
- Test: `expense-api/tests/test_tra_delete.py` (extend)

**Interfaces:**
- Consumes: `expense_crud.delete_claim` from Task 1.
- Produces: `def can_delete_claim(claim, user_id: uuid.UUID, role: str) -> bool`, used again in Task 3.
- Produces: `DELETE /api/v1/expenses/{claim_id}` → `204`, or `404` / `403` / `409`.

- [ ] **Step 1: Write the failing tests**

Append to `expense-api/tests/test_tra_delete.py`:

```python
_DELETABLE = ("draft", "returned", "submitted", "in_review")


@pytest.mark.parametrize("status", _DELETABLE)
@pytest.mark.asyncio
async def test_owner_deletes_unapproved_tra(status):
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, status)
    async with _client_for("employee", owner) as client:
        resp = await client.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 204, resp.text
    assert not await _exists(claim_id)


@pytest.mark.parametrize("status", ["approved", "cancelled", "rejected", "paid"])
@pytest.mark.asyncio
async def test_cannot_delete_once_past_approval(status):
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, status)
    async with _client_for("employee", owner) as client:
        resp = await client.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 409
    assert status in resp.json()["detail"]
    assert await _exists(claim_id)


@pytest.mark.asyncio
async def test_non_tra_claim_types_are_not_deletable():
    """EXP/MIL/TRV carry budget and payment consequences — out of scope."""
    owner = str(uuid.uuid4())
    async with db_module.AsyncSessionLocal() as db:
        exp = ExpenseClaim(
            claim_number=f"EXP-TEST-{uuid.uuid4().hex[:8]}", claim_type="EXP",
            employee_id=uuid.UUID(owner), employee_name="Owner",
            department_name="Ops", submission_date=date(2026, 8, 3),
            status="draft", created_by=uuid.UUID(owner),
        )
        db.add(exp)
        await db.commit()
        claim_id = str(exp.id)

    async with _client_for("employee", owner) as client:
        resp = await client.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 409
    assert await _exists(claim_id)


@pytest.mark.asyncio
async def test_other_employee_cannot_delete():
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "draft")
    async with _client_for("employee", str(uuid.uuid4())) as stranger:
        resp = await stranger.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 403
    assert await _exists(claim_id)


@pytest.mark.asyncio
async def test_approver_cannot_delete_someone_elses_tra():
    """Approvers have return/reject — deletion is the applicant's call."""
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "submitted")
    async with _client_for("dept_manager", str(uuid.uuid4())) as approver:
        resp = await approver.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 403
    assert await _exists(claim_id)


@pytest.mark.asyncio
async def test_system_admin_can_delete_anyones_tra():
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "submitted")
    async with _client_for("system_admin", str(uuid.uuid4())) as admin:
        resp = await admin.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 204
    assert not await _exists(claim_id)


@pytest.mark.asyncio
async def test_missing_claim_is_404():
    async with _client_for("employee", str(uuid.uuid4())) as client:
        resp = await client.delete(f"/api/v1/expenses/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tra_referenced_by_a_trv_is_refused():
    """Defence, not a live path: the TRV gate requires an APPROVED TRA and
    approved is not deletable. But travel_application_id is ON DELETE SET NULL,
    so if those rules ever drift a submitted reimbursement would silently lose
    its authorization basis."""
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "draft")
    async with db_module.AsyncSessionLocal() as db:
        trv = ExpenseClaim(
            claim_number=f"TRV-TEST-{uuid.uuid4().hex[:8]}", claim_type="TRV",
            employee_id=uuid.UUID(owner), employee_name="Owner",
            department_name="Ops", submission_date=date(2026, 8, 3),
            status="submitted", created_by=uuid.UUID(owner),
            travel_application_id=uuid.UUID(claim_id),
        )
        db.add(trv)
        await db.commit()
        trv_id = str(trv.id)

    async with _client_for("employee", owner) as client:
        resp = await client.delete(f"/api/v1/expenses/{claim_id}")
    assert resp.status_code == 409
    assert await _exists(claim_id)

    # cleanup
    async with db_module.AsyncSessionLocal() as db:
        await db.delete(await db.get(ExpenseClaim, uuid.UUID(trv_id)))
        await db.delete(await db.get(ExpenseClaim, uuid.UUID(claim_id)))
        await db.commit()
```

- [ ] **Step 2: Run and watch them fail**

```bash
cd c:/Project/uniops-tra-delete
docker cp expense-api/tests uniops_expense_api://tmp/wt/tests
MSYS_NO_PATHCONV=1 docker exec -w /tmp/wt uniops_expense_api python -m pytest tests/test_tra_delete.py -q 2>&1 | tail -5
```

Expected: the new tests fail with `405 Method Not Allowed` (the route does not exist).

- [ ] **Step 3: Add the helper and the endpoint**

In `expense-api/app/api/v1/expenses.py`, near the other module-level helpers:

```python
_DELETABLE_STATUSES = ("draft", "returned", "submitted", "in_review")


def can_delete_claim(claim, user_id: uuid.UUID, role: str) -> bool:
    """Whether `user_id` may hard-delete `claim`. Pure — no queries.

    Deliberately does not check for a referencing TRV: that would be one query
    per row on every list render, for a case the status rule already makes
    unreachable. The DELETE endpoint runs that check.
    """
    if claim.claim_type != "TRA":
        return False
    if claim.status not in _DELETABLE_STATUSES:
        return False
    return role == "system_admin" or claim.employee_id == user_id
```

Then the endpoint, after `update_expense`:

```python
@router.delete("/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_travel_application(
    claim_id: uuid.UUID,
    db: SessionDep,
    user: CurrentUserDep,
    token: BearerTokenDep,
):
    """Hard-delete an unapproved Travel Application.

    Restricted to TRA: EXP/MIL/TRV carry budget and payment consequences, so
    this does not open hard delete for them. The record is gone for good —
    approval history included — and the claim number is retired (numbering
    takes max-suffix + 1 and never reuses a gap).
    """
    from sqlalchemy import func, select as sa_select
    from app.models.expense import ExpenseClaim as EC

    claim = await expense_crud.get_by_id(db, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Expense claim not found")

    user_id = uuid.UUID(user["sub"])
    role = user.get("role", "")

    if claim.claim_type != "TRA":
        raise HTTPException(
            status_code=409,
            detail=f"Only Travel Applications can be deleted, not {claim.claim_type}",
        )
    if claim.status not in _DELETABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete a Travel Application in status '{claim.status}'",
        )
    if not can_delete_claim(claim, user_id, role):
        raise HTTPException(
            status_code=403,
            detail="Only the applicant can delete this Travel Application",
        )

    referencing = (await db.execute(
        sa_select(func.count()).select_from(EC)
        .where(EC.travel_application_id == claim_id)
    )).scalar_one()
    if referencing:
        raise HTTPException(
            status_code=409,
            detail="This Travel Application is referenced by a travel expense claim",
        )

    # Drop attachment blobs before the rows cascade away, otherwise file-api
    # accumulates orphans. Best-effort: the claim going away matters more.
    for att in claim.attachments:
        if att.file_id:
            try:
                await delete_from_file_server(uuid.UUID(att.file_id), token)
            except Exception:
                logger.warning("file-api delete failed for %s; continuing", att.file_id)

    await expense_crud.delete_claim(db, claim)
    await db.commit()
```

Order matters: the type and status checks come **before** the ownership check so a stranger probing a paid claim gets the same 409 an owner does, and the owner of an approved TRA gets a message about status rather than a misleading 403.

Two things this endpoint needs are not yet in the file (verified: `BearerTokenDep` **is** already imported on line 9; `logging` and `delete_from_file_server` are not). Add at the top:

```python
import logging

from app.services.attachment_helper import delete_from_file_server

logger = logging.getLogger(__name__)
```

- [ ] **Step 4: Run the file's tests**

```bash
cd c:/Project/uniops-tra-delete
docker cp expense-api/app/api/v1/expenses.py uniops_expense_api://tmp/wt/app/api/v1/expenses.py
MSYS_NO_PATHCONV=1 docker exec -w /tmp/wt uniops_expense_api python -m pytest tests/test_tra_delete.py -q 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 5: Run the whole backend suite**

```bash
MSYS_NO_PATHCONV=1 docker exec -w /tmp/wt uniops_expense_api python -m pytest -q 2>&1 | tail -3
```

Expected: `0 failed`, total = 146 + the tests added so far. A route added under `/{claim_id}` can shadow sibling routes — this run is what proves it did not.

- [ ] **Step 6: Commit**

```bash
git add expense-api/app/api/v1/expenses.py expense-api/tests/test_tra_delete.py
git commit -m "feat(oa): DELETE endpoint for unapproved Travel Applications

Hard-deletes a TRA in draft/returned/submitted/in_review for its
applicant or a system_admin. Refuses non-TRA types, approved and terminal
statuses, and any TRA a TRV still references. Attachment blobs are
dropped from file-api first so no orphans are left behind."
```

---

### Task 3: Surface `can_delete` to the frontend

**Files:**
- Modify: `expense-api/app/schemas/expense.py` (`ClaimPermissions`, `ExpenseClaimListItem`)
- Modify: `expense-api/app/api/v1/expenses.py` (`list_expenses` both branches, `get_claim_permissions`)
- Test: `expense-api/tests/test_tra_delete.py` (extend)

**Interfaces:**
- Consumes: `can_delete_claim` from Task 2.
- Produces: `can_delete: bool` on every `ExpenseClaimListItem` and on `ClaimPermissions`.

- [ ] **Step 1: Write the failing tests**

Append to `expense-api/tests/test_tra_delete.py`:

```python
@pytest.mark.asyncio
async def test_list_marks_own_draft_deletable_and_others_not():
    owner = str(uuid.uuid4())
    mine = await _seed_tra(owner, "draft")
    theirs = await _seed_tra(str(uuid.uuid4()), "draft")

    async with _client_for("system_admin", str(uuid.uuid4())) as admin:
        admin_rows = {i["id"]: i for i in (
            await admin.get("/api/v1/expenses", params={"type": "TRA", "page_size": 100})
        ).json()["items"]}
    assert admin_rows[mine]["can_delete"] is True
    assert admin_rows[theirs]["can_delete"] is True, "system_admin may delete anyone's"

    async with _client_for("employee", owner) as client:
        rows = {i["id"]: i for i in (
            await client.get("/api/v1/expenses", params={"type": "TRA"})
        ).json()["items"]}
    assert rows[mine]["can_delete"] is True
    assert theirs not in rows, "a co-worker's draft is not visible at all"

    async with db_module.AsyncSessionLocal() as db:
        for cid in (mine, theirs):
            await db.delete(await db.get(ExpenseClaim, uuid.UUID(cid)))
        await db.commit()


@pytest.mark.asyncio
async def test_list_marks_approved_tra_not_deletable():
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "approved")
    async with _client_for("employee", owner) as client:
        rows = {i["id"]: i for i in (
            await client.get("/api/v1/expenses", params={"type": "TRA"})
        ).json()["items"]}
    assert rows[claim_id]["can_delete"] is False

    async with db_module.AsyncSessionLocal() as db:
        await db.delete(await db.get(ExpenseClaim, uuid.UUID(claim_id)))
        await db.commit()


@pytest.mark.asyncio
async def test_permissions_endpoint_reports_can_delete():
    owner = str(uuid.uuid4())
    claim_id = await _seed_tra(owner, "submitted")

    async with _client_for("employee", owner) as client:
        mine = (await client.get(f"/api/v1/expenses/{claim_id}/permissions")).json()
    assert mine["can_delete"] is True

    async with _client_for("dept_manager", str(uuid.uuid4())) as approver:
        theirs = (await approver.get(f"/api/v1/expenses/{claim_id}/permissions")).json()
    assert theirs["can_delete"] is False

    async with db_module.AsyncSessionLocal() as db:
        await db.delete(await db.get(ExpenseClaim, uuid.UUID(claim_id)))
        await db.commit()
```

- [ ] **Step 2: Run and watch them fail**

```bash
cd c:/Project/uniops-tra-delete
docker cp expense-api/tests uniops_expense_api://tmp/wt/tests
MSYS_NO_PATHCONV=1 docker exec -w /tmp/wt uniops_expense_api python -m pytest tests/test_tra_delete.py -q -k "can_delete or deletable" 2>&1 | tail -5
```

Expected: FAIL with `KeyError: 'can_delete'`.

- [ ] **Step 3: Add the schema fields**

In `expense-api/app/schemas/expense.py`, add to `ExpenseClaimListItem` (after `created_at`):

```python
    # Filled in by list_expenses per row — the list page renders its delete
    # button from this and holds no permission logic of its own.
    can_delete: bool = False
```

and to `ClaimPermissions`:

```python
    can_delete: bool = False
```

- [ ] **Step 4: Fill it in on both list branches**

In `list_expenses`, the `system_admin` / `ap_clerk` branch currently builds its response with a comprehension. Replace the item construction so each row is stamped:

```python
        return ExpenseClaimListResponse(
            items=[_list_item(c, user_id, role) for c in items],
            total=total,
        )
```

and likewise the role-based branch's `paged` comprehension:

```python
    return ExpenseClaimListResponse(
        items=[_list_item(c, user_id, role) for c in paged],
        total=total,
    )
```

Add the shared builder next to `can_delete_claim`:

```python
def _list_item(claim, user_id: uuid.UUID, role: str) -> "ExpenseClaimListItem":
    from app.schemas.expense import ExpenseClaimListItem

    item = ExpenseClaimListItem.model_validate(claim)
    item.can_delete = can_delete_claim(claim, user_id, role)
    return item
```

- [ ] **Step 5: Report it from the permissions endpoint**

In `get_claim_permissions`, extend the return:

```python
    return ClaimPermissions(
        is_owner=is_owner,
        can_approve=can_approve,
        can_pay=can_pay,
        can_delete=can_delete_claim(claim, user_id, role),
    )
```

- [ ] **Step 6: Run the suite**

```bash
cd c:/Project/uniops-tra-delete
docker cp expense-api/app uniops_expense_api://tmp/wt/app
MSYS_NO_PATHCONV=1 docker exec -w /tmp/wt uniops_expense_api python -m pytest -q 2>&1 | tail -3
```

Expected: `0 failed`.

- [ ] **Step 7: Commit**

```bash
git add expense-api/app/schemas/expense.py expense-api/app/api/v1/expenses.py expense-api/tests/test_tra_delete.py
git commit -m "feat(oa): expose can_delete on claim list rows and permissions

One pure helper drives the DELETE gate, the permissions endpoint and every
list row, so the frontend renders its delete affordance from a flag and
carries no role logic."
```

---

### Task 4: Confirm dialog + delete button on the Travel Applications list

**Files:**
- Create: `oa/src/components/ui/ConfirmDialog.tsx`
- Modify: `oa/src/pages/travel/TravelApplicationsListPage.tsx`

**Interfaces:**
- Consumes: `can_delete` on each list row (Task 3), `DELETE /api/v1/expenses/{id}` (Task 2).
- Produces: `<ConfirmDialog title message confirmLabel onConfirm onClose loading error />`, reused by Task 5.

- [ ] **Step 1: Install dependencies in the worktree**

```bash
cd c:/Project/uniops-tra-delete/oa && npm ci --no-audit --no-fund
```

Without this, `tsc` prints an install prompt instead of type errors and every later check is a false negative.

- [ ] **Step 2: Create the dialog**

`oa/src/components/ui/ConfirmDialog.tsx` — modelled on `ActionModal.tsx` (same portal + card treatment) minus the comment box, since a hard delete records no reason:

```tsx
import { createPortal } from 'react-dom'
import { AlertTriangle, X } from 'lucide-react'

export function ConfirmDialog({
  title, message, confirmLabel = 'Delete', onConfirm, onClose, loading, error,
}: {
  title: string
  message: string
  confirmLabel?: string
  onConfirm: () => void
  onClose: () => void
  loading: boolean
  error?: string
}) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-neutral-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-danger-50">
              <AlertTriangle className="h-5 w-5 text-danger-600" />
            </div>
            <h2 className="text-sm font-semibold text-neutral-900">{title}</h2>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="px-6 py-5 flex flex-col gap-4">
          <p className="text-sm text-neutral-600">{message}</p>
          {error && (
            <div className="flex items-center gap-2 rounded-lg bg-danger-50 border border-danger-200 px-3 py-2 text-sm text-danger-700">
              <AlertTriangle className="h-4 w-4 shrink-0" />{error}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button
              onClick={onClose}
              className="rounded-lg border border-neutral-200 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
            >
              Cancel
            </button>
            <button
              onClick={onConfirm}
              disabled={loading}
              className="rounded-lg bg-danger-600 px-4 py-2 text-sm font-medium text-white hover:bg-danger-700 disabled:opacity-50"
            >
              {loading ? 'Deleting…' : confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
```

- [ ] **Step 3: Wire it into the list page**

In `oa/src/pages/travel/TravelApplicationsListPage.tsx`:

Add `can_delete: boolean` to the `TravelApp` interface. Import `useMutation`, `useQueryClient`, `Trash2`, and `ConfirmDialog`. Inside the component:

```tsx
  const qc = useQueryClient()
  const [pendingDelete, setPendingDelete] = useState<TravelApp | null>(null)
  const [deleteError, setDeleteError] = useState('')

  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/expenses/${id}`),
    onSuccess: () => {
      // Deleting the only row on a trailing page would otherwise strand the
      // user on an empty page.
      if ((data?.items.length ?? 0) === 1 && page > 1) setPage(page - 1)
      qc.invalidateQueries({ queryKey: ['travel-list'] })
      setPendingDelete(null)
    },
    onError: (e: unknown) => setDeleteError(e instanceof Error ? e.message : 'Delete failed'),
  })
```

Add a trailing header cell:

```tsx
                <th className="px-4 py-3 text-right font-medium text-neutral-500 text-xs uppercase tracking-wide">Actions</th>
```

and the matching body cell as the last `<td>` in the row:

```tsx
                  <td className="px-4 py-3 text-right">
                    {a.can_delete && (
                      <button
                        onClick={(e) => { e.stopPropagation(); setDeleteError(''); setPendingDelete(a) }}
                        title="Delete this travel application"
                        className="rounded-lg p-1.5 text-neutral-400 hover:bg-danger-50 hover:text-danger-600"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </td>
```

`stopPropagation` is required — the `<tr>` navigates to the detail page on click.

Render the dialog at the end of the component's returned fragment:

```tsx
      {pendingDelete && (
        <ConfirmDialog
          title={`Delete ${pendingDelete.claim_number}?`}
          message="This travel application and its approval history will be permanently deleted. This cannot be undone."
          onConfirm={() => del.mutate(pendingDelete.id)}
          onClose={() => setPendingDelete(null)}
          loading={del.isPending}
          error={deleteError}
        />
      )}
```

`api.delete` is defined at `oa/src/lib/api.ts:108` and the `danger-*` palette is already in use (`AppLayout.tsx`) — both verified, no setup needed.

- [ ] **Step 4: Type-check**

```bash
cd c:/Project/uniops-tra-delete/oa
npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
```

Expected: no output (0 errors).

- [ ] **Step 5: Commit**

```bash
git add oa/src/components/ui/ConfirmDialog.tsx oa/src/pages/travel/TravelApplicationsListPage.tsx
git commit -m "feat(oa): delete button on the Travel Applications list

Renders from the row's server-computed can_delete flag; confirmation
spells out that the application and its approval history are gone for
good."
```

---

### Task 5: Delete button on the Travel Application detail page

**Files:**
- Modify: `oa/src/pages/expenses/ExpenseDetailPage.tsx`

**Interfaces:**
- Consumes: `can_delete` from `/permissions` (Task 3), `ConfirmDialog` (Task 4).

- [ ] **Step 1: Read the permission flag**

`/travel/:id` renders `ExpenseDetailPage`. Extend the permissions query's type parameter (around line 331) with `can_delete: boolean`, and add alongside the other derived flags near line 381:

```tsx
  const canDelete = perms?.can_delete ?? false
```

The flag is false for EXP/MIL/TRV server-side, so the same component stays correct for reimbursements without a client-side type check.

- [ ] **Step 2: Add the button and dialog**

Import `Trash2` and `ConfirmDialog`, and add state next to the existing action state:

```tsx
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleteError, setDeleteError] = useState('')

  const deleteClaim = useMutation({
    mutationFn: () => api.delete(`/api/v1/expenses/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['travel-list'] })
      navigate('/travel')
    },
    onError: (e: unknown) => setDeleteError(e instanceof Error ? e.message : 'Delete failed'),
  })
```

In the header's action button row, after the existing buttons:

```tsx
            {canDelete && (
              <button
                onClick={() => { setDeleteError(''); setConfirmingDelete(true) }}
                className="inline-flex items-center gap-2 rounded-lg border border-danger-200 px-4 py-2 text-sm font-medium text-danger-600 hover:bg-danger-50"
              >
                <Trash2 className="h-4 w-4" />
                Delete
              </button>
            )}
```

and at the end of the component, next to the existing action modal:

```tsx
      {confirmingDelete && (
        <ConfirmDialog
          title={`Delete ${claim.claim_number}?`}
          message="This travel application and its approval history will be permanently deleted. This cannot be undone."
          onConfirm={() => deleteClaim.mutate()}
          onClose={() => setConfirmingDelete(false)}
          loading={deleteClaim.isPending}
          error={deleteError}
        />
      )}
```

The existing `deleteMutation` in this file is the attachment delete — do not reuse that name.

- [ ] **Step 3: Type-check**

```bash
cd c:/Project/uniops-tra-delete/oa
npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
```

Expected: no output.

- [ ] **Step 4: Final full backend run**

```bash
cd c:/Project/uniops-tra-delete
docker cp expense-api/app uniops_expense_api://tmp/wt/app
docker cp expense-api/tests uniops_expense_api://tmp/wt/tests
MSYS_NO_PATHCONV=1 docker exec -w /tmp/wt uniops_expense_api python -m pytest -q 2>&1 | tail -3
```

Expected: `0 failed`.

- [ ] **Step 5: Commit and clean up the container staging area**

```bash
git add oa/src/pages/expenses/ExpenseDetailPage.tsx
git commit -m "feat(oa): delete button on the Travel Application detail page

Rendered from the server's can_delete flag, so the shared detail
component stays correct for EXP/MIL/TRV without a client-side type check."
docker exec uniops_expense_api sh -c 'rm -rf /tmp/wt'
```

---

## Manual verification

Not covered by the automated tests — worth a pass in the dev stack:

1. Create a travel application, leave it in Draft. The list shows a trash icon; the Expense Claims list does not show the application at all (that is the fix already on `fix/oa-expense-list-excludes-tra`).
2. Delete it from the list. The row disappears, the number is not reused by the next application.
3. Create another, submit it, then delete it from the detail page. Sign in as the approver: the approve task is gone from Task Inbox — this is the orphaned-task failure mode the shared-refs purge exists to prevent.
4. Approve a third one. Both the trash icon and the detail-page Delete button are gone.
