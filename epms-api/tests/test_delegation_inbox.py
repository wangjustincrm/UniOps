"""Task 7 — delegated approval tasks reach the delegate's inbox, and the
delegate can open the underlying document.

Covers the six scenarios in the task-7 brief:
  1. Inside the window, the delegate's inbox contains the delegator's PINNED
     approve task (assigned_user_id = delegator).
  2. Inside the window, the delegate's inbox contains a ROLE-POOL approve task
     (assigned_user_id IS NULL, assigned_role = a role the delegator holds).
  3. Outside the window, neither appears.
  4. NON-approval tasks (type 'create_pa') never appear, in or out of window.
  5. The delegate can OPEN the delegator's PR (build_scope includes it) inside
     the window, and cannot outside it.
  6. The delegate's own visibility scope is otherwise UNCHANGED: delegating
     from a gm must NOT make the delegate unrestricted (security regression
     guard) — the delegate still cannot see an unrelated department's PR.

Tasks are inserted directly via the ORM rather than driven through the
approval engine — this file is only exercising the inbox/visibility widening,
not approval routing itself.
"""
import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.delegation import local_today
from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.models.department import Department
from app.models.task import Task
from app.schemas.auth import RegisterRequest

TASKS_URL = "/api/v1/tasks"
PR_URL = "/api/v1/pr"

_PR_BASE = {
    "title": "Delegation Inbox Test PR",
    "type": 3,
    "currency": "CAD",
    "line_items": [
        {"description": "Part A", "material_id": "M-001",
         "qty": "1", "unit": "EA", "unit_price": "10.00"}
    ],
}


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _make_user(test_engine, role: str, department_id: str | None = None) -> str:
    """Create a user in the test DB, return its id."""
    factory = _factory(test_engine)
    async with factory() as db:
        user = await user_crud.create(
            db,
            RegisterRequest(
                email=f"{role}-{uuid.uuid4().hex[:6]}@deleg-test.com",
                password="TestPass1!",
                full_name=f"Deleg {role}",
                role=role,
            ),
        )
        if department_id is not None:
            user.department_id = uuid.UUID(department_id)
        await db.commit()
        return str(user.id)


async def _make_dept(test_engine) -> str:
    factory = _factory(test_engine)
    code = f"D{uuid.uuid4().hex[:4].upper()}"
    async with factory() as db:
        dept = Department(code=code, name=f"Dept {code}", is_active=True)
        db.add(dept)
        await db.commit()
        await db.refresh(dept)
        return str(dept.id)


async def _make_cc(test_engine, dept_id: str) -> str:
    """Insert a cost center in the given department. Returns id."""
    from app.models.cost_center import CostCenter

    factory = _factory(test_engine)
    code = f"CC{uuid.uuid4().hex[:4].upper()}"
    async with factory() as db:
        cc = CostCenter(code=code, name=f"CostCenter {code}", is_active=True,
                         department_id=uuid.UUID(dept_id))
        db.add(cc)
        await db.commit()
        await db.refresh(cc)
        return str(cc.id)


def _authed_client(user_id: str, role: str) -> AsyncClient:
    token = create_access_token(user_id, role)
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _seed_delegation(
    test_engine, *, delegator_id: str, delegate_id: str, start_date, end_date,
) -> None:
    """Insert a row straight into the approval_delegations shadow table."""
    factory = _factory(test_engine)
    async with factory() as db:
        await db.execute(sa.text(
            "INSERT INTO approval_delegations "
            "(id, delegator_user_id, delegate_user_id, start_date, end_date, created_by) "
            "VALUES (:id, :delegator, :delegate, :start, :end, :delegator)"),
            {
                "id": str(uuid.uuid4()),
                "delegator": delegator_id,
                "delegate": delegate_id,
                "start": start_date,
                "end": end_date,
            })
        await db.commit()


async def _make_task(
    test_engine, *,
    type: str,
    document_type: str = "pr",
    document_id: uuid.UUID | None = None,
    document_number: str = "PR-DELEG-TEST",
    assigned_role: str,
    assigned_user_id: str | None = None,
) -> uuid.UUID:
    factory = _factory(test_engine)
    doc_id = document_id or uuid.uuid4()
    async with factory() as db:
        task = Task(
            type=type,
            document_type=document_type,
            document_id=doc_id,
            document_number=document_number,
            assigned_role=assigned_role,
            assigned_user_id=uuid.UUID(assigned_user_id) if assigned_user_id else None,
            title=f"Test task {type}",
            is_completed=False,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task.id


async def _inbox_task_ids(user_id: str, role: str) -> set[str]:
    async with _authed_client(user_id, role) as c:
        resp = await c.get(TASKS_URL)
        assert resp.status_code == 200, resp.text
        return {item["id"] for item in resp.json()["items"]}


# ── 1. Pinned approve task reaches the delegate, inside the window ────────────

@pytest.mark.asyncio
async def test_pinned_approve_task_reaches_delegate_inside_window(test_engine):
    delegator_id = await _make_user(test_engine, "dept_manager")
    delegate_id = await _make_user(test_engine, "requester")

    task_id = await _make_task(
        test_engine,
        type="approve_pr",
        assigned_role="dept_manager",
        assigned_user_id=delegator_id,
    )

    today = local_today()
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today - timedelta(days=1), end_date=today + timedelta(days=1),
    )

    ids = await _inbox_task_ids(delegate_id, "requester")
    assert str(task_id) in ids, (
        "delegate's inbox did not contain the delegator's pinned approve_pr task "
        "inside the delegation window"
    )


# ── 2. Role-pool approve task reaches the delegate, inside the window ─────────

@pytest.mark.asyncio
async def test_role_pool_approve_task_reaches_delegate_inside_window(test_engine):
    # procurement_manager is a broadcast (role-pool) approval role: the
    # delegator holds it as their PRIMARY role, so delegated_broadcast_roles
    # picks it up.
    delegator_id = await _make_user(test_engine, "procurement_manager")
    delegate_id = await _make_user(test_engine, "requester")

    task_id = await _make_task(
        test_engine,
        type="approve_po",
        document_type="po",
        document_number="PO-DELEG-TEST",
        assigned_role="procurement_manager",
        assigned_user_id=None,
    )

    today = local_today()
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today, end_date=today,
    )

    ids = await _inbox_task_ids(delegate_id, "requester")
    assert str(task_id) in ids, (
        "delegate's inbox did not contain the delegator's role-pool approve_po "
        "task inside the delegation window"
    )


# ── 3. Outside the window, neither pinned nor role-pool task appears ──────────

@pytest.mark.asyncio
async def test_tasks_excluded_outside_delegation_window(test_engine):
    delegator_id = await _make_user(test_engine, "procurement_manager")
    delegate_id = await _make_user(test_engine, "requester")

    pinned_task_id = await _make_task(
        test_engine,
        type="approve_pr",
        assigned_role="dept_manager",
        assigned_user_id=delegator_id,
    )
    pool_task_id = await _make_task(
        test_engine,
        type="approve_po",
        document_type="po",
        document_number="PO-DELEG-TEST-2",
        assigned_role="procurement_manager",
        assigned_user_id=None,
    )

    today = local_today()
    # Delegation window already ended.
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today - timedelta(days=10), end_date=today - timedelta(days=1),
    )

    ids = await _inbox_task_ids(delegate_id, "requester")
    assert str(pinned_task_id) not in ids, (
        "delegate saw the delegator's pinned task OUTSIDE the delegation window"
    )
    assert str(pool_task_id) not in ids, (
        "delegate saw the delegator's role-pool task OUTSIDE the delegation window"
    )


# ── 4. Non-approval tasks never appear via delegation, in or out of window ────

@pytest.mark.asyncio
async def test_non_approval_task_never_reaches_delegate(test_engine):
    delegator_id = await _make_user(test_engine, "dept_manager")
    delegate_id = await _make_user(test_engine, "requester")

    # create_pa is a role-pool task type but NOT an approval task — delegation
    # must never surface it, no matter how the window is set.
    create_pa_task_id = await _make_task(
        test_engine,
        type="create_pa",
        document_type="po",
        document_number="PO-DELEG-CREATEPA",
        assigned_role="dept_manager",
        assigned_user_id=delegator_id,
    )

    today = local_today()
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today, end_date=today,
    )

    ids = await _inbox_task_ids(delegate_id, "requester")
    assert str(create_pa_task_id) not in ids, (
        "a non-approval task (create_pa) leaked into the delegate's inbox — "
        "delegation must cover approve* task types only"
    )


# ── 5. The delegate can open the delegator's PR inside the window only ────────

@pytest.mark.asyncio
async def test_delegate_can_open_delegators_pr_inside_window_only(test_engine):
    dept_pr = await _make_dept(test_engine)
    dept_delegate = await _make_dept(test_engine)  # unrelated to dept_pr

    creator_id = await _make_user(test_engine, "requester", department_id=dept_pr)
    delegator_id = await _make_user(test_engine, "dept_manager", department_id=dept_pr)
    delegate_id = await _make_user(test_engine, "requester", department_id=dept_delegate)
    delegate_outside_id = await _make_user(test_engine, "requester", department_id=dept_delegate)

    async with _authed_client(creator_id, "requester") as c:
        r = await c.post(PR_URL, json=_PR_BASE)
        assert r.status_code == 201, r.text
        pr = r.json()
        pr_id = pr["id"]
        pr_number = pr["number"]

    # Baseline: without delegation, an unrelated requester (different dept,
    # not the creator) cannot open this PR.
    async with _authed_client(delegate_id, "requester") as c:
        resp = await c.get(f"{PR_URL}/{pr_id}")
        assert resp.status_code == 404, (
            "test setup invalid: delegate could already see the PR before delegation"
        )

    await _make_task(
        test_engine,
        type="approve_pr",
        document_id=uuid.UUID(pr_id),
        document_number=pr_number,
        assigned_role="dept_manager",
        assigned_user_id=delegator_id,
    )

    today = local_today()
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today, end_date=today,
    )
    # A second delegate whose window has already expired.
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_outside_id,
        start_date=today - timedelta(days=10), end_date=today - timedelta(days=1),
    )

    async with _authed_client(delegate_id, "requester") as c:
        resp = await c.get(f"{PR_URL}/{pr_id}")
        assert resp.status_code == 200, (
            f"delegate could not open the delegator's PR inside the delegation "
            f"window (status={resp.status_code})"
        )

    async with _authed_client(delegate_outside_id, "requester") as c:
        resp = await c.get(f"{PR_URL}/{pr_id}")
        assert resp.status_code == 404, (
            f"delegate (expired window) could open the delegator's PR "
            f"(status={resp.status_code})"
        )


# ── 6. Security regression guard: delegation must not widen the delegate's ────
#       own visibility scope (_effective_role_codes must stay untouched).

@pytest.mark.asyncio
async def test_delegation_does_not_widen_delegate_own_scope(test_engine):
    """Delegating from a gm must NOT make the delegate unrestricted.

    gm's own scope is derived from `_mapped_dept_ids('gm')` — set up the gm
    delegator to cover a department, then prove the delegate (a plain
    requester with no relation to that department, and no task on its PR)
    still cannot see a PR from it. If a future change ever fed the
    delegator's roles into the delegate's `_effective_role_codes` (instead of
    routing them only through `delegated_broadcast_roles` for task queries),
    this assertion would flip to a 200 and catch it.
    """
    from app.crud import config as config_crud

    factory = _factory(test_engine)
    async with factory() as db:
        await config_crud.get_or_create(db)
        await db.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS approval_dept_routing ("
            " dept_id uuid PRIMARY KEY,"
            " gm_or_opm varchar(3) NOT NULL DEFAULT 'gm',"
            " director_user_id uuid NULL,"
            " supervisor_enabled boolean NOT NULL DEFAULT false,"
            " updated_by uuid NULL,"
            " updated_at timestamptz NOT NULL DEFAULT now()"
            ")"
        ))
        await db.commit()

    dept_gm = await _make_dept(test_engine)  # mapped to the gm delegator
    cc_gm = await _make_cc(test_engine, dept_gm)  # gm scope is cost-center-based, not requester-dept-based
    delegator_id = await _make_user(test_engine, "gm")
    delegate_id = await _make_user(test_engine, "requester")

    async with factory() as db:
        await db.execute(sa.text("DELETE FROM approval_dept_routing WHERE dept_id = :d"), {"d": dept_gm})
        await db.execute(sa.text(
            "INSERT INTO approval_dept_routing (dept_id, gm_or_opm, supervisor_enabled) "
            "VALUES (:d, 'gm', false)"), {"d": dept_gm})
        await db.commit()

    creator_id = await _make_user(test_engine, "requester")
    async with _authed_client(creator_id, "requester") as c:
        r = await c.post(PR_URL, json={**_PR_BASE, "cost_center_id": cc_gm})
        assert r.status_code == 201, r.text
        pr_id = r.json()["id"]

    # Sanity: the gm delegator really does cover this PR through their own
    # mapped-department scope (no task involved).
    async with _authed_client(delegator_id, "gm") as c:
        resp = await c.get(f"{PR_URL}/{pr_id}")
        assert resp.status_code == 200, "test setup invalid: gm cannot see its own mapped-dept PR"

    today = local_today()
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today, end_date=today,
    )

    # The delegate has no task for this PR and no department relation to it —
    # only the (irrelevant, task-only) delegation from the gm. Their own scope
    # must stay exactly "requester", not gm's mapped-department scope.
    async with _authed_client(delegate_id, "requester") as c:
        resp = await c.get(f"{PR_URL}/{pr_id}")
        assert resp.status_code == 404, (
            f"delegate gained the gm delegator's own visibility scope via "
            f"delegation — _effective_role_codes must not be widened by "
            f"delegation (status={resp.status_code})"
        )
        resp_list = await c.get(PR_URL)
        assert resp_list.status_code == 200
        ids = [p["id"] for p in resp_list.json()["items"]]
        assert pr_id not in ids, (
            "delegate's PR list leaked the gm delegator's mapped-department PR"
        )
