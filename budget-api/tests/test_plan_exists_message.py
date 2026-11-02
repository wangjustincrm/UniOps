"""BPLAN-003: creating a plan for a (cost center, year) that already has a
current one is refused. ISS-0115: the refusal names the cost center the way the
New Plan picker shows it and says what to do instead — no internal ids."""
import uuid

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from app.crud import plan as plan_crud
from app.models.plan import BudgetPlan
from app.schemas.plan import PlanCreate

pytestmark = pytest.mark.asyncio


async def _cc(db, code="SELL-0113", name="SELL-ECOM"):
    cc_id = uuid.uuid4()
    await db.execute(
        sa.text("INSERT INTO cost_centers (id, code, name, department_id, is_active) "
                "VALUES (:id, :code, :name, :dept, true)"),
        {"id": cc_id, "code": code, "name": name, "dept": uuid.uuid4()},
    )
    return cc_id


async def _plan(db, cc_id, status):
    plan = BudgetPlan(cost_center_id=cc_id, fiscal_year=2026, status=status,
                      is_current=True, version=1, created_by=uuid.uuid4())
    db.add(plan)
    await db.flush()
    return plan


async def _refusal(db, cc_id) -> str:
    with pytest.raises(HTTPException) as exc:
        await plan_crud.create_plan(db, PlanCreate(cost_center_id=cc_id, fiscal_year=2026), uuid.uuid4())
    assert exc.value.status_code == 409
    return exc.value.detail


async def test_approved_plan_refusal_names_the_cost_center_and_revise(db_session):
    cc_id = await _cc(db_session)
    existing = await _plan(db_session, cc_id, "approved")

    detail = await _refusal(db_session, cc_id)
    assert detail == ("A FY 2026 plan already exists for SELL-0113 — SELL-ECOM. "
                      "Open it and use Revise Plan to change it.")
    assert str(cc_id) not in detail and str(existing.id) not in detail


async def test_draft_plan_refusal_says_continue_with_it(db_session):
    # Revise Plan is only offered on an approved current plan; a draft v1 is
    # simply the plan still being written.
    cc_id = await _cc(db_session, code="CC-DRAFT", name="Draft Center")
    await _plan(db_session, cc_id, "in_review")

    detail = await _refusal(db_session, cc_id)
    assert detail.startswith("A FY 2026 plan already exists for CC-DRAFT — Draft Center. ")
    assert "Version v1 is In Review" in detail
    assert "Revise Plan" not in detail


async def test_unknown_cost_center_falls_back_without_ids(db_session):
    # A plan whose cost center row is not readable still gets a plain message,
    # and the session stays usable afterwards (the lookup runs in a savepoint).
    cc_id = uuid.uuid4()
    await _plan(db_session, cc_id, "approved")

    detail = await _refusal(db_session, cc_id)
    assert detail.startswith("A FY 2026 plan already exists for this cost center.")
    assert str(cc_id) not in detail
    assert (await db_session.execute(sa.text("SELECT 1"))).scalar() == 1
