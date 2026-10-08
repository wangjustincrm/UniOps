"""finance_director — the final payment sign-off after Finance Manager on the PA chain.

It is a company-unique post (workflow._POST_CODES), granted as an ADDITIONAL
role: the holder keeps their own login role. The step must therefore resolve
to whoever holds the post regardless of the PA's department — unlike the
`director` step, which follows the document's department and would hand a
Marketing PA to the Marketing director.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.crud.engine import _actor_can_approve, execute_action
from app.crud.workflow import get_dept_gm_opm_mapping, get_role_management
from app.models.config import CompanyConfig
from app.models.pa import PaymentApplication
from app.models.routing import DeptRouting
from app.models.task import Task
from app.models.user import User


async def _seed(db):
    """A Marketing requester whose department has its OWN director, a Finance
    Manager, and the Finance Director (primary role `director`, post held as
    an additional role)."""
    marketing, finance = uuid.uuid4(), uuid.uuid4()
    requester = User(full_name="Requester", id=uuid.uuid4(), role="requester",
                     department_id=marketing, is_active=True)
    mkt_director = User(full_name="Marketing Director", id=uuid.uuid4(), role="director",
                        department_id=marketing, is_active=True)
    fin_manager = User(full_name="Finance Manager", id=uuid.uuid4(), role="finance_manager",
                       department_id=finance, is_active=True)
    fin_director = User(full_name="Finance Director", id=uuid.uuid4(), role="director",
                        department_id=finance, is_active=True)
    db.add_all([requester, mkt_director, fin_manager, fin_director])
    await db.flush()
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_director')"),
        {"u": str(fin_director.id)})
    db.add(DeptRouting(dept_id=marketing, gm_or_opm="gm",
                       director_user_id=mkt_director.id, supervisor_enabled=False))
    db.add(DeptRouting(dept_id=finance, gm_or_opm="gm",
                       director_user_id=fin_director.id, supervisor_enabled=False))
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"pa": [
        {"id": "finance_mgr", "role": "finance_manager", "label": "Finance Manager"},
        {"id": "finance_director", "role": "finance_director", "label": "Finance Director"},
    ]}))
    await db.flush()
    return requester, mkt_director, fin_manager, fin_director


async def _make_pa(db, requester):
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-FD-{uuid.uuid4().hex[:4]}",
        title="Finance Director sign-off", status="submitted", approval_step_idx=0,
        payment_amount=Decimal("100.00"), vendor_name="V", currency="CAD",
        invoice_ids=[], po_id=None, created_by=requester.id)
    db.add(pa)
    await db.flush()
    return pa


async def _can_approve(db, actor, actor_role, dept_id):
    rm = await get_role_management(db)
    return await _actor_can_approve(
        db, "finance_director", actor.id, actor_role, dept_id, rm,
        await get_dept_gm_opm_mapping(db), set())


@pytest.mark.asyncio
async def test_post_holder_can_approve_on_another_departments_pa(engine_db_session):
    db = engine_db_session
    requester, _, _, fin_director = await _seed(db)
    assert await _can_approve(db, fin_director, "director", requester.department_id)


@pytest.mark.asyncio
async def test_non_holders_are_denied(engine_db_session):
    """Neither the document department's own director nor the Finance Manager
    holds the post — the step must not fall back to either."""
    db = engine_db_session
    requester, mkt_director, fin_manager, _ = await _seed(db)
    assert not await _can_approve(db, mkt_director, "director", requester.department_id)
    assert not await _can_approve(db, fin_manager, "finance_manager", requester.department_id)


@pytest.mark.asyncio
async def test_pa_waits_for_finance_director_then_approves(engine_db_session):
    db = engine_db_session
    requester, _, fin_manager, fin_director = await _seed(db)
    pa = await _make_pa(db, requester)

    await execute_action(db, "pa", pa.id, "approve", fin_manager.id, "finance_manager")

    assert pa.status == "in_review"
    assert pa.approval_step_idx == 1, "Finance Manager's approval is no longer final"
    task = (await db.execute(sa.select(Task).where(
        Task.document_id == pa.id, Task.is_completed.is_(False)))).scalar_one()
    assert task.assigned_role == "finance_director"
    assert task.assigned_user_id is None, (
        "a post step is broadcast to the current holder, like finance_manager — "
        "pinning it to a user would orphan it when the post changes hands")

    await execute_action(db, "pa", pa.id, "approve", fin_director.id, "director")
    assert pa.status == "approved"
