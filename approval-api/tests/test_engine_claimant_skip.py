"""An OA claim never lands on its own claimant.

expense-api refuses the claimant Approve on their own claim (OA.md §7.3), but
the engine routes by role: a Department Manager's own Travel Application put
step 1 (Department Manager) on the claimant, and nobody could move it
(TRA-20261020-0001/0002). A step whose ONLY holder is the claimant is now
skipped — at submit, in the cascade after an approval, and on re-sync for
claims already stuck. EPMS documents are untouched (EPMS.md §3.3.5 keeps
self-approval at submit there).
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.crud.engine import (CLAIMANT_SKIP_REASON, MSG_CLAIMANT_HOLDS_EVERY_STEP,
                             _resync_document, execute_action, preflight_submit)
from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.expense import ExpenseClaim
from app.models.pa import PaymentApplication
from app.models.task import Task
from app.models.user import User

_TRA_CHAIN = [
    {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
    {"id": "finance_mgr", "role": "finance_manager", "label": "Finance Manager"},
    {"id": "gm", "role": "gm", "label": "General Manager"},
]


async def _seed(db, dept_id, chain=None):
    users = {
        "employee": User(full_name="Employee", id=uuid.uuid4(), role="requester",
                         department_id=dept_id, is_active=True),
        "dm": User(full_name="Dept Manager", id=uuid.uuid4(), role="dept_manager",
                   department_id=dept_id, is_active=True),
        "fm": User(full_name="Finance Manager", id=uuid.uuid4(), role="finance_manager",
                   department_id=dept_id, is_active=True),
        "gm": User(full_name="GM", id=uuid.uuid4(), role="gm", is_active=True),
    }
    db.add_all(users.values())
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={
        "tra": chain or _TRA_CHAIN,
        "pa": [{"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
               {"id": "gm", "role": "gm", "label": "General Manager"}],
    }))
    await db.flush()
    return users


async def _claim(db, claimant, status="draft", step=0):
    c = ExpenseClaim(id=uuid.uuid4(), claim_number=f"TRA-T-{uuid.uuid4().hex[:6]}",
                     claim_type="TRA", status=status, approval_step_idx=step,
                     total_amount=Decimal("0"), employee_name=claimant.full_name,
                     created_by=claimant.id)
    db.add(c)
    await db.flush()
    return c


async def _events(db, doc_id):
    return (await db.execute(sa.select(ApprovalEvent).where(
        ApprovalEvent.document_id == doc_id).order_by(ApprovalEvent.created_at,
                                                      ApprovalEvent.step_idx))).scalars().all()


async def _open_tasks(db, doc_id):
    return (await db.execute(sa.select(Task).where(
        Task.document_id == doc_id, Task.is_completed.is_(False)))).scalars().all()


@pytest.mark.asyncio
async def test_dept_manager_own_tra_goes_to_finance_manager(engine_db_session):
    db = engine_db_session
    u = await _seed(db, uuid.uuid4())
    tra = await _claim(db, u["dm"])

    await execute_action(db, "tra", tra.id, "submit", u["dm"].id, "dept_manager")

    assert tra.status == "submitted"
    assert tra.approval_step_idx == 1
    skip = [e for e in await _events(db, tra.id) if e.action == "approve"]
    assert [(e.step_idx, e.comment) for e in skip] == [(0, CLAIMANT_SKIP_REASON)]
    tasks = await _open_tasks(db, tra.id)
    assert len(tasks) == 1
    assert tasks[0].assigned_role == "finance_manager"
    assert tasks[0].assigned_user_id != u["dm"].id


@pytest.mark.asyncio
async def test_employee_tra_still_starts_at_dept_manager(engine_db_session):
    db = engine_db_session
    u = await _seed(db, uuid.uuid4())
    tra = await _claim(db, u["employee"])

    await execute_action(db, "tra", tra.id, "submit", u["employee"].id, "requester")

    assert tra.approval_step_idx == 0
    assert [e.action for e in await _events(db, tra.id)] == ["submit"]
    tasks = await _open_tasks(db, tra.id)
    assert [t.assigned_user_id for t in tasks] == [u["dm"].id]


@pytest.mark.asyncio
async def test_later_step_held_by_claimant_is_skipped_after_approval(engine_db_session):
    db = engine_db_session
    u = await _seed(db, uuid.uuid4())
    tra = await _claim(db, u["fm"])   # the Finance Manager's own TRA

    await execute_action(db, "tra", tra.id, "submit", u["fm"].id, "finance_manager")
    assert tra.approval_step_idx == 0
    await execute_action(db, "tra", tra.id, "approve", u["dm"].id, "dept_manager")

    assert tra.status == "in_review"
    assert tra.approval_step_idx == 2
    comments = {e.step_idx: e.comment for e in await _events(db, tra.id) if e.action == "approve"}
    assert comments[1] == CLAIMANT_SKIP_REASON
    assert [t.assigned_role for t in await _open_tasks(db, tra.id)] == ["gm"]


@pytest.mark.asyncio
async def test_step_with_another_holder_is_not_skipped(engine_db_session):
    db = engine_db_session
    u = await _seed(db, uuid.uuid4())
    # A second Finance Manager: the claimant is not the only one who can act.
    db.add(User(full_name="Finance Manager 2", id=uuid.uuid4(), role="finance_manager",
                is_active=True))
    await db.flush()
    tra = await _claim(db, u["fm"])

    await execute_action(db, "tra", tra.id, "submit", u["fm"].id, "finance_manager")
    await execute_action(db, "tra", tra.id, "approve", u["dm"].id, "dept_manager")

    assert tra.approval_step_idx == 1
    assert [t.assigned_role for t in await _open_tasks(db, tra.id)] == ["finance_manager"]


@pytest.mark.asyncio
async def test_claimant_holding_every_step_is_refused(engine_db_session):
    db = engine_db_session
    u = await _seed(db, uuid.uuid4(), chain=[_TRA_CHAIN[0]])
    tra = await _claim(db, u["dm"])

    checks = await preflight_submit(db, "tra", tra.id)
    routing = next(c for c in checks if c.id == "approver_routing")
    assert routing.passed is False and routing.message == MSG_CLAIMANT_HOLDS_EVERY_STEP

    with pytest.raises(ValueError, match="approved by the claimant"):
        await execute_action(db, "tra", tra.id, "submit", u["dm"].id, "dept_manager")
    assert tra.status == "draft"
    assert await _events(db, tra.id) == []


@pytest.mark.asyncio
async def test_resync_moves_a_claim_stuck_on_its_claimant(engine_db_session):
    db = engine_db_session
    u = await _seed(db, uuid.uuid4())
    # The production shape: submitted before the fix, step 0 pinned to the claimant.
    tra = await _claim(db, u["dm"], status="submitted", step=0)
    db.add(Task(id=uuid.uuid4(), type="approve_tra", priority="normal", document_type="tra",
                document_id=tra.id, document_number=tra.claim_number,
                assigned_role="dept_manager", assigned_user_id=u["dm"].id,
                title="Approve TRA", is_completed=False))
    await db.flush()

    summary = await _resync_document(db, "tra", tra.id)

    assert summary is not None and summary["final_step"] == 1
    assert tra.status == "in_review"
    assert [t.assigned_role for t in await _open_tasks(db, tra.id)] == ["finance_manager"]


@pytest.mark.asyncio
async def test_epms_document_keeps_self_approval_at_submit(engine_db_session):
    db = engine_db_session
    u = await _seed(db, uuid.uuid4())
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-T-{uuid.uuid4().hex[:4]}", title="DM's own PA",
        status="draft", approval_step_idx=0, payment_amount=Decimal("10"),
        vendor_name="V", currency="CAD", invoice_ids=[], po_id=None, created_by=u["dm"].id)
    db.add(pa)
    await db.flush()

    await execute_action(db, "pa", pa.id, "submit", u["dm"].id, "dept_manager")

    assert pa.approval_step_idx == 0
    assert [t.assigned_user_id for t in await _open_tasks(db, pa.id)] == [u["dm"].id]
