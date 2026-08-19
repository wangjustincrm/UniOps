"""A delegate may approve in the delegator's place; both retain the right."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.crud.engine import _actor_can_approve, execute_action
from app.crud.workflow import get_dept_gm_opm_mapping, get_role_management
from app.models.config import CompanyConfig
from app.models.delegation import ApprovalDelegation
from app.models.event import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.routing import DeptRouting
from app.models.user import User

TODAY = date(2026, 8, 25)
START, END = date(2026, 8, 20), date(2026, 9, 3)


async def _seed(db, dept_id, *, delegate_start=START, delegate_end=END):
    requester = User(full_name="Test User", id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
    manager = User(full_name="Test User", id=uuid.uuid4(), role="dept_manager", department_id=dept_id, is_active=True)
    stand_in = User(full_name="Test User", id=uuid.uuid4(), role="dept_manager", is_active=True)
    db.add_all([requester, manager, stand_in])
    await db.flush()
    db.add(DeptRouting(dept_id=dept_id, gm_or_opm="gm", supervisor_enabled=False))
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"pa": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"}]}))
    db.add(ApprovalDelegation(
        id=uuid.uuid4(), delegator_user_id=manager.id, delegate_user_id=stand_in.id,
        start_date=delegate_start, end_date=delegate_end, created_by=uuid.uuid4()))
    await db.flush()
    return requester, manager, stand_in


async def _ctx(db):
    rm = await get_role_management(db)
    return rm, await get_dept_gm_opm_mapping(db), {
        uuid.UUID(u) for u in rm.get("finance_bp_user_ids", [])}


@pytest.mark.asyncio
async def test_delegate_is_authorized_inside_the_window(engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    _, manager, stand_in = await _seed(db, dept_id)
    rm, mapping, fbp = await _ctx(db)
    assert await _actor_can_approve(
        db, "dept_manager", stand_in.id, "dept_manager", dept_id, rm, mapping, fbp,
        today=TODAY)


@pytest.mark.asyncio
async def test_delegate_is_denied_outside_the_window(engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    _, manager, stand_in = await _seed(db, dept_id)
    rm, mapping, fbp = await _ctx(db)
    assert not await _actor_can_approve(
        db, "dept_manager", stand_in.id, "dept_manager", dept_id, rm, mapping, fbp,
        today=date(2026, 9, 4))


@pytest.mark.asyncio
async def test_delegator_keeps_the_right(engine_db_session):
    """Both can approve — the decision was 'first click wins', not a transfer."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    _, manager, stand_in = await _seed(db, dept_id)
    rm, mapping, fbp = await _ctx(db)
    assert await _actor_can_approve(
        db, "dept_manager", manager.id, "dept_manager", dept_id, rm, mapping, fbp,
        today=TODAY)


@pytest.mark.asyncio
async def test_system_admin_is_never_inherited(engine_db_session):
    """Delegating FROM an admin must not hand over the unconditional bypass."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    admin = User(full_name="Test User", id=uuid.uuid4(), role="system_admin", is_active=True)
    nobody = User(full_name="Test User", id=uuid.uuid4(), role="requester", is_active=True)
    db.add_all([admin, nobody])
    await db.flush()
    db.add(ApprovalDelegation(
        id=uuid.uuid4(), delegator_user_id=admin.id, delegate_user_id=nobody.id,
        start_date=START, end_date=END, created_by=uuid.uuid4()))
    await db.flush()
    rm, mapping, fbp = await _ctx(db)
    assert not await _actor_can_approve(
        db, "dept_manager", nobody.id, "requester", dept_id, rm, mapping, fbp,
        today=TODAY), "an admin's delegate must not inherit the admin bypass"


@pytest.mark.asyncio
async def test_approval_event_records_the_delegate_and_names_the_delegator(
        engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester, manager, stand_in = await _seed(db, dept_id)
    manager.full_name = "Sivers"
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-DG-{uuid.uuid4().hex[:4]}",
        title="Delegated approval", status="submitted", approval_step_idx=0,
        payment_amount=Decimal("100.00"), vendor_name="V", currency="CAD",
        invoice_ids=[], po_id=None, created_by=requester.id)
    db.add(pa)
    await db.flush()

    await execute_action(db, "pa", pa.id, "approve", stand_in.id, "dept_manager",
                         today=TODAY)

    ev = (await db.execute(sa.select(ApprovalEvent).where(
        ApprovalEvent.document_id == pa.id))).scalars().one()
    assert ev.actor_id == stand_in.id, "the person who clicked is the actor"
    assert "on behalf of Sivers" in (ev.comment or "")


@pytest.mark.asyncio
async def test_reject_event_also_names_the_delegator(engine_db_session):
    """A delegate's REJECT is an approval-step decision too — it must carry the
    same on-behalf-of audit annotation as approve, not a silently blank one."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester, manager, stand_in = await _seed(db, dept_id)
    manager.full_name = "Sivers"
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-DG-{uuid.uuid4().hex[:4]}",
        title="Delegated rejection", status="submitted", approval_step_idx=0,
        payment_amount=Decimal("100.00"), vendor_name="V", currency="CAD",
        invoice_ids=[], po_id=None, created_by=requester.id)
    db.add(pa)
    await db.flush()

    await execute_action(db, "pa", pa.id, "reject", stand_in.id, "dept_manager",
                         today=TODAY)

    ev = (await db.execute(sa.select(ApprovalEvent).where(
        ApprovalEvent.document_id == pa.id))).scalars().one()
    assert ev.actor_id == stand_in.id, "the person who clicked is the actor"
    assert ev.action == "reject"
    assert "on behalf of Sivers" in (ev.comment or "")
