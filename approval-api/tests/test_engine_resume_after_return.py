"""Resubmitting a returned document resumes at the step that returned it.

It used to restart at step 0: `return` resets approval_step_idx, and `submit`
always walked the chain from the top, so everyone who had already approved had
to approve again — EXP-20260929-0001 went back to its first approver with four
of five steps signed. Company rule (2026-10-14, every document type): those
approvals stand, and the resubmission goes straight back to whoever returned it.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

import sqlalchemy as sa

from app.crud.engine import execute_action, preflight_submit
from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.task import Task
from app.models.user import User


async def _seed(db, steps=2):
    """A requester and a PA chain of named posts with distinct holders, so no
    step is auto-skipped as same-approver."""
    requester = User(full_name="Requester", id=uuid.uuid4(), role="requester", is_active=True)
    fin_manager = User(full_name="Finance Manager", id=uuid.uuid4(), role="finance_manager",
                       is_active=True)
    fin_director = User(full_name="Finance Director", id=uuid.uuid4(), role="director",
                        is_active=True)
    db.add_all([requester, fin_manager, fin_director])
    await db.flush()
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_director')"),
        {"u": str(fin_director.id)})
    workflow = [
        {"id": "finance_mgr", "role": "finance_manager", "label": "Finance Manager"},
        {"id": "finance_director", "role": "finance_director", "label": "Finance Director"},
    ][:steps]
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"pa": workflow}))
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-RS-{uuid.uuid4().hex[:4]}",
        title="Resume after return", status="draft", approval_step_idx=0,
        payment_amount=Decimal("100.00"), vendor_name="V", currency="CAD",
        invoice_ids=[], po_id=None, created_by=requester.id)
    db.add(pa)
    await db.flush()
    return pa, requester, fin_manager, fin_director


async def _open_approve_tasks(db, doc_id):
    # By type: submit leaves the revise task open (the next approve closes it),
    # as it always has — not what these tests are about.
    return (await db.execute(sa.select(Task).where(
        Task.document_id == doc_id, Task.is_completed.is_(False),
        Task.type == "approve_pa"))).scalars().all()


async def _returned_at_step_1(db):
    pa, requester, fin_manager, fin_director = await _seed(db)
    await execute_action(db, "pa", pa.id, "submit", requester.id, "requester")
    await execute_action(db, "pa", pa.id, "approve", fin_manager.id, "finance_manager")
    await execute_action(db, "pa", pa.id, "return", fin_director.id, "director",
                         comment="wrong amount")
    assert pa.status == "returned"
    assert pa.approval_step_idx == 0, "return still resets the index; the event carries the step"
    return pa, requester, fin_manager, fin_director


async def test_resubmit_goes_back_to_the_step_that_returned_it(engine_db_session):
    db = engine_db_session
    pa, requester, _fm, fin_director = await _returned_at_step_1(db)

    await execute_action(db, "pa", pa.id, "submit", requester.id, "requester")

    assert pa.status == "submitted"
    assert pa.approval_step_idx == 1
    (task,) = await _open_approve_tasks(db, pa.id)
    assert task.assigned_role == "finance_director"

    # The step-0 approval stands: one more approve finishes the chain.
    await execute_action(db, "pa", pa.id, "approve", fin_director.id, "director")
    assert pa.status == "approved"


async def test_preflight_routes_the_resumed_step(engine_db_session):
    """preflight_submit must check the approver the submission will actually
    reach, not step 0's."""
    db = engine_db_session
    pa, _r, _fm, _fd = await _returned_at_step_1(db)
    checks = {c.id: c for c in await preflight_submit(db, "pa", pa.id)}
    assert checks["status_allows_submit"].passed
    assert checks["approver_routing"].passed


async def test_returned_at_first_step_starts_at_first_step(engine_db_session):
    db = engine_db_session
    pa, requester, fin_manager, _fd = await _seed(db)
    await execute_action(db, "pa", pa.id, "submit", requester.id, "requester")
    await execute_action(db, "pa", pa.id, "return", fin_manager.id, "finance_manager")

    await execute_action(db, "pa", pa.id, "submit", requester.id, "requester")
    assert pa.approval_step_idx == 0
    (task,) = await _open_approve_tasks(db, pa.id)
    assert task.assigned_role == "finance_manager"


async def test_latest_return_wins(engine_db_session):
    """Returned at step 1, resumed, then returned again — the later return is
    the one that counts. Every action in this test shares one transaction, so
    now() gives them all one created_at; the first return is backdated to make
    the order real."""
    db = engine_db_session
    pa, requester, fin_manager, _fd = await _returned_at_step_1(db)
    await db.execute(sa.update(ApprovalEvent)
                     .where(ApprovalEvent.document_id == pa.id, ApprovalEvent.action == "return")
                     .values(created_at=ApprovalEvent.created_at - timedelta(minutes=5)))
    # Rewind to step 0 by hand to stage a later return from there.
    pa.status, pa.approval_step_idx = "submitted", 0
    await db.flush()
    await execute_action(db, "pa", pa.id, "return", fin_manager.id, "finance_manager")

    await execute_action(db, "pa", pa.id, "submit", requester.id, "requester")
    assert pa.approval_step_idx == 0


async def test_step_beyond_a_shortened_workflow_starts_over(engine_db_session):
    """The chain lost the step that returned it → nothing to resume to; start
    at the top rather than skip straight past every approver."""
    db = engine_db_session
    pa, requester, _fm, _fd = await _returned_at_step_1(db)
    cfg = (await db.execute(sa.select(CompanyConfig))).scalar_one()
    cfg.workflow_defs = {"pa": cfg.workflow_defs["pa"][:1]}
    await db.flush()

    await execute_action(db, "pa", pa.id, "submit", requester.id, "requester")
    assert pa.status == "submitted"
    assert pa.approval_step_idx == 0


async def test_recalled_document_starts_over(engine_db_session):
    """Recall makes it a draft again — only `returned` resumes."""
    db = engine_db_session
    pa, requester, _fm, _fd = await _returned_at_step_1(db)
    await execute_action(db, "pa", pa.id, "submit", requester.id, "requester")
    assert pa.approval_step_idx == 1
    await execute_action(db, "pa", pa.id, "recall", requester.id, "requester")
    assert pa.status == "draft"

    await execute_action(db, "pa", pa.id, "submit", requester.id, "requester")
    assert pa.approval_step_idx == 0
