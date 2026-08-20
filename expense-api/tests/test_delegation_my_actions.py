"""Task 11 — delegated approval tasks reach the delegate in OA (expense-api).

Covers the five scenarios in the task-11 brief:
  1. Inside the window, the delegator's pinned approve task appears in the
     delegate's my-actions.
  2. A role-pool approve task for a role the delegator holds appears too.
  3. Outside the window, neither appears.
  4. _can_act_on_claim is True for the delegate inside the window and False
     outside it.
  5. _can_act_on_claim still behaves identically for a PA (the pa.py caller).

Tasks are inserted directly via the ORM/raw SQL rather than driven through
the approval engine — this file exercises only the inbox/gate widening, not
approval routing itself.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.base as db_module
from app.api.v1.expenses import _can_act_on_claim
from app.core.delegation import local_today
from app.models.expense import ExpenseClaim
from app.models.pa import PaymentApplication
from app.models.task_mirror import TaskMirror
from tests.conftest import _client, _make_token

MY_ACTIONS = "/api/v1/expenses/my-actions"


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_claim(test_engine, *, claim_type: str = "EXP", status: str = "in_review",
                       step_idx: int = 0) -> uuid.UUID:
    factory = _factory(test_engine)
    cid = uuid.uuid4()
    emp = uuid.uuid4()
    async with factory() as s:
        s.add(ExpenseClaim(
            id=cid, claim_number=f"EC-DELEG-{cid.hex[:8]}", claim_type=claim_type,
            employee_id=emp, employee_name="Delegation Test Employee",
            submission_date=date(2026, 8, 14), status=status,
            approval_step_idx=step_idx, created_by=emp,
        ))
        await s.commit()
    return cid


async def _seed_pa(test_engine, *, status: str = "in_review") -> uuid.UUID:
    factory = _factory(test_engine)
    pid = uuid.uuid4()
    creator = uuid.uuid4()
    async with factory() as s:
        s.add(PaymentApplication(
            id=pid, pa_number=f"PA-DELEG-{pid.hex[:8]}", title="Delegation Test PA",
            po_id=None, agreement_id=None,
            vendor_id=uuid.uuid4(), vendor_name="Delegation Vendor",
            invoice_ids=[], gr_ids=[], pa_type="regular",
            subtotal=Decimal("100.00"), payment_amount=Decimal("100.00"),
            currency="CAD", status=status, created_by=creator,
        ))
        await s.commit()
    return pid


async def _add_task(document_id: uuid.UUID, *, document_type: str = "exp",
                     user_id: str | None = None, role: str | None = None,
                     task_type: str = "approve_exp") -> None:
    async with db_module.AsyncSessionLocal() as db:
        db.add(TaskMirror(
            id=uuid.uuid4(), document_id=document_id, document_type=document_type,
            type=task_type,
            assigned_user_id=uuid.UUID(user_id) if user_id else None,
            assigned_role=role, is_completed=False,
            created_at=datetime.now(timezone.utc),
        ))
        await db.commit()


async def _seed_user(test_engine, *, role: str = "dept_manager") -> uuid.UUID:
    factory = _factory(test_engine)
    uid = uuid.uuid4()
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO users (id, full_name, email, role, is_active) "
            "VALUES (:id, 'Delegation Test User', :email, :role, true)"),
            {"id": uid, "email": f"u-{uid.hex[:8]}@example.com", "role": role})
        await db.commit()
    return uid


async def _seed_delegation(test_engine, *, delegator_id, delegate_id,
                            start_date, end_date) -> None:
    factory = _factory(test_engine)
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO approval_delegations "
            "(id, delegator_user_id, delegate_user_id, start_date, end_date, created_by) "
            "VALUES (:id, :delegator, :delegate, :start, :end, :delegator)"),
            {
                "id": uuid.uuid4(), "delegator": delegator_id,
                "delegate": delegate_id, "start": start_date, "end": end_date,
            })
        await db.commit()


async def _inbox_ids(role: str, user_id: str) -> set[str]:
    async with _client(_make_token(role, user_id)) as c:
        r = await c.get(MY_ACTIONS)
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"]}


# ── 1. Pinned approve task reaches the delegate, inside the window ────────────

@pytest.mark.asyncio
async def test_delegated_pinned_task_reaches_delegate_inside_window(test_engine):
    delegator_id = await _seed_user(test_engine, role="dept_manager")
    delegate_id = await _seed_user(test_engine, role="requester")

    cid = await _seed_claim(test_engine)
    await _add_task(cid, user_id=str(delegator_id))

    today = local_today()
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today - timedelta(days=1), end_date=today + timedelta(days=1),
    )

    ids = await _inbox_ids("requester", str(delegate_id))
    assert str(cid) in ids, (
        "delegate's my-actions did not contain the delegator's pinned approve "
        "task inside the delegation window"
    )


# ── 2. Role-pool approve task reaches the delegate, inside the window ─────────

@pytest.mark.asyncio
async def test_delegated_role_pool_task_reaches_delegate_inside_window(test_engine):
    delegator_id = await _seed_user(test_engine, role="finance_bp")
    delegate_id = await _seed_user(test_engine, role="requester")

    cid = await _seed_claim(test_engine)
    await _add_task(cid, role="finance_bp")

    today = local_today()
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today, end_date=today,
    )

    ids = await _inbox_ids("requester", str(delegate_id))
    assert str(cid) in ids, (
        "delegate's my-actions did not contain the delegator's role-pool "
        "approve task inside the delegation window"
    )


# ── 3. Outside the window, neither pinned nor role-pool task appears ──────────

@pytest.mark.asyncio
async def test_delegated_tasks_excluded_outside_window(test_engine):
    delegator_id = await _seed_user(test_engine, role="finance_bp")
    delegate_id = await _seed_user(test_engine, role="requester")

    pinned_cid = await _seed_claim(test_engine)
    await _add_task(pinned_cid, user_id=str(delegator_id))

    pool_cid = await _seed_claim(test_engine)
    await _add_task(pool_cid, role="finance_bp")

    today = local_today()
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today - timedelta(days=10), end_date=today - timedelta(days=1),
    )

    ids = await _inbox_ids("requester", str(delegate_id))
    assert str(pinned_cid) not in ids, (
        "delegate saw the delegator's pinned task OUTSIDE the delegation window"
    )
    assert str(pool_cid) not in ids, (
        "delegate saw the delegator's role-pool task OUTSIDE the delegation window"
    )


# ── 4. _can_act_on_claim: True inside the window, False outside it ────────────

@pytest.mark.asyncio
async def test_can_act_on_claim_true_inside_false_outside_window(test_engine):
    delegator_id = await _seed_user(test_engine, role="dept_manager")
    delegate_id = await _seed_user(test_engine, role="requester")

    cid = await _seed_claim(test_engine)
    await _add_task(cid, user_id=str(delegator_id))

    today = local_today()
    factory = _factory(test_engine)

    # Outside the window first — no delegation row exists yet.
    async with factory() as db:
        claim = await db.get(ExpenseClaim, cid)
        got = await _can_act_on_claim(db, claim, delegate_id, "requester")
    assert got is False, "delegate could act on the claim before any delegation existed"

    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today - timedelta(days=10), end_date=today - timedelta(days=1),
    )
    async with factory() as db:
        claim = await db.get(ExpenseClaim, cid)
        got = await _can_act_on_claim(db, claim, delegate_id, "requester")
    assert got is False, "delegate could act on the claim OUTSIDE the delegation window"

    # A second delegation row, this one live today.
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today, end_date=today,
    )
    async with factory() as db:
        claim = await db.get(ExpenseClaim, cid)
        got = await _can_act_on_claim(db, claim, delegate_id, "requester")
    assert got is True, "delegate could not act on the claim INSIDE the delegation window"


# ── 5. _can_act_on_claim behaves identically for a PA (pa.py's caller) ────────

@pytest.mark.asyncio
async def test_can_act_on_claim_for_pa_matches_claim_behavior(test_engine):
    delegator_id = await _seed_user(test_engine, role="dept_manager")
    delegate_id = await _seed_user(test_engine, role="requester")

    pid = await _seed_pa(test_engine)
    await _add_task(pid, document_type="pa_dir", user_id=str(delegator_id), task_type="approve_pa")

    factory = _factory(test_engine)

    # Before any delegation window — must be False.
    async with factory() as db:
        pa = await db.get(PaymentApplication, pid)
        got = await _can_act_on_claim(db, pa, delegate_id, "requester")
    assert got is False, "delegate could act on the PA before any delegation existed"

    today = local_today()
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today, end_date=today,
    )
    async with factory() as db:
        pa = await db.get(PaymentApplication, pid)
        # pa.py's own caller (get_pa_permissions) invokes _can_act_on_claim
        # with no `role` argument — mirror that call shape here.
        got = await _can_act_on_claim(db, pa, delegate_id)
    assert got is True, (
        "delegate could not act on the PA INSIDE the delegation window — "
        "_can_act_on_claim must behave identically for PAs and expense claims"
    )
