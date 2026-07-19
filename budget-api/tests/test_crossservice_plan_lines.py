"""current_plan_lines — budget column source for finance's predreal grid.

Only lines from the CURRENT APPROVED plan of each (cc, fy) are returned; drafts
and superseded revisions are excluded.
"""
import uuid
from decimal import Decimal

import pytest

from app.crud import plan as plan_crud
from app.models.catalog import BudgetAccount, BudgetL1
from app.models.plan import BudgetPlan, BudgetPlanLine

pytestmark = pytest.mark.asyncio


async def test_current_plan_lines_only_current_approved(db_session):
    db = db_session
    l1 = BudgetL1(code="L1", name="Ops", sort_order=0)
    db.add(l1)
    await db.flush()
    acct = BudgetAccount(code="CRM003", name="IT General Fee", l1_id=l1.id, sort_order=0)
    db.add(acct)
    await db.flush()

    cc = uuid.uuid4()
    approved = BudgetPlan(cost_center_id=cc, fiscal_year=2026, status="approved",
                          is_current=True, version=1, created_by=uuid.uuid4())
    draft = BudgetPlan(cost_center_id=uuid.uuid4(), fiscal_year=2026, status="draft",
                       is_current=False, version=1, created_by=uuid.uuid4())
    db.add_all([approved, draft])
    await db.flush()
    db.add_all([
        BudgetPlanLine(plan_id=approved.id, account_id=acct.id, month=6, amount=Decimal("1000")),
        BudgetPlanLine(plan_id=approved.id, account_id=acct.id, month=7, amount=Decimal("5")),
        BudgetPlanLine(plan_id=draft.id, account_id=acct.id, month=6, amount=Decimal("999")),
    ])
    await db.flush()

    rows = await plan_crud.current_plan_lines(db, 2026, 6)
    assert rows == [(cc, acct.id, Decimal("1000"))]
