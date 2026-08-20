"""The same-approver auto-skip must recognise a multi-holder post.

A Department Manager who ALSO holds GM through an additional user_roles role
is a legitimate holder of the gm_or_opm step, but is not
_post_holders()['gm'][0] when the real GM sorts first by uid. The skip walk
used to compare against that collapsed first holder, so it did not fire and
the same person had to approve the document twice.

Sibling of test_engine_multiholder_approve.py, which fixed the same drift in
_actor_can_approve and left the skip walk behind.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.crud.engine import execute_action
from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.routing import DeptRouting
from app.models.user import User

# gm_first sorts before actor by uid string, so gm_first is _post_holders()['gm'][0].
_GM_FIRST_ID = uuid.UUID(int=1)
_ACTOR_ID = uuid.UUID(int=(1 << 128) - 1)


async def _seed(db, dept_id):
    requester = User(full_name="Test User", id=uuid.uuid4(), role="requester",
                     department_id=dept_id, is_active=True)
    gm_first = User(full_name="Test User", id=_GM_FIRST_ID, role="gm", is_active=True)
    # The actor is BOTH this department's manager and a GM holder.
    actor = User(full_name="Test User", id=_ACTOR_ID, role="dept_manager",
                 department_id=dept_id, is_active=True)
    db.add_all([requester, gm_first, actor])
    await db.flush()
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'gm')"),
        {"u": str(actor.id)})
    db.add(DeptRouting(dept_id=dept_id, gm_or_opm="gm", supervisor_enabled=False))
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"pa": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "gm_or_opm", "role": "gm_or_opm", "label": "GM / OPM"},
    ]}))
    await db.flush()
    return requester, actor


async def _make_pa(db, requester, prefix):
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-{prefix}-{uuid.uuid4().hex[:4]}",
        title="Same-approver skip", status="submitted", approval_step_idx=0,
        payment_amount=Decimal("100.00"), vendor_name="V", currency="CAD",
        invoice_ids=[], po_id=None, created_by=requester.id)
    db.add(pa)
    await db.flush()
    return pa


@pytest.mark.asyncio
async def test_second_step_auto_skips_for_non_first_post_holder(engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester, actor = await _seed(db, dept_id)
    pa = await _make_pa(db, requester, "SK")

    await execute_action(db, "pa", pa.id, "approve", actor.id, "dept_manager")

    assert pa.status == "approved", (
        "the gm_or_opm step must auto-skip: the approver holds that post too, "
        "even though another holder sorts first"
    )
    events = (await db.execute(
        sa.select(ApprovalEvent)
        .where(ApprovalEvent.document_id == pa.id)
        .order_by(ApprovalEvent.step_idx))).scalars().all()
    assert events[1].comment == "Auto-approved (same approver holds both roles)"


@pytest.mark.asyncio
async def test_second_step_does_not_skip_for_a_different_person(engine_db_session):
    """Negative guard — the fix must not degrade into 'always skip'."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester, actor = await _seed(db, dept_id)
    # Strip the actor's GM role: now the two steps are two different people.
    await db.execute(sa.text(
        "DELETE FROM user_roles WHERE user_id = :u"), {"u": str(actor.id)})
    pa = await _make_pa(db, requester, "NS")

    await execute_action(db, "pa", pa.id, "approve", actor.id, "dept_manager")

    assert pa.status == "in_review"
    assert pa.approval_step_idx == 1, "the GM step must still be pending"
