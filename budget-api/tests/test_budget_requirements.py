"""Tests for the three Budget refinements:

  Req1 — get_monthly_actuals_summary: per-account plan vs actual by month.
  Req2 — import_plan_csv: decomposed accounts accept a direct month total,
         clearing that cell's factor breakdowns.
  Req3 — opening-balance import: per (cc, account, month) opening rows count
         toward actual_spent and re-import upserts rather than duplicates.
"""
import uuid
from decimal import Decimal

import pytest

from app.crud import balance as balance_crud
from app.crud import opening as opening_crud
from app.crud import plan as plan_crud
from app.crud.ledger import get_actual_spent
from app.models.catalog import BudgetAccount, BudgetL1
from app.models.ledger import BudgetLedger
from app.models.plan import BudgetPlan, BudgetPlanBreakdown, BudgetPlanLine

pytestmark = pytest.mark.asyncio

CC = uuid.uuid4()
FY = 2026


async def _seed_catalog(db, *, decomposed=False):
    l1 = BudgetL1(code="L1", name="Marketing", sort_order=0)
    db.add(l1)
    await db.flush()
    acct = BudgetAccount(
        code="ACC1", name="Ads", l1_id=l1.id, sort_order=0,
        decomposition_enabled=decomposed,
    )
    db.add(acct)
    await db.flush()
    return l1, acct


async def _seed_approved_plan(db, acct, *, month_amounts: dict[int, Decimal]):
    plan = BudgetPlan(
        cost_center_id=CC, fiscal_year=FY, status="approved",
        is_current=True, version=1, created_by=uuid.uuid4(),
    )
    db.add(plan)
    await db.flush()
    for m, amt in month_amounts.items():
        db.add(BudgetPlanLine(plan_id=plan.id, account_id=acct.id, month=m, amount=amt))
    await db.flush()
    return plan


# ── Req1 ──────────────────────────────────────────────────────────────────────

async def test_monthly_summary_plan_and_actual_by_month(db_session):
    db = db_session
    _l1, acct = await _seed_catalog(db)
    await _seed_approved_plan(db, acct, month_amounts={1: Decimal("100"), 2: Decimal("200")})
    # actuals: book_expense in Jan, opening in Feb (both count as actual)
    db.add(BudgetLedger(
        source_service="expense", source_doc_type="claim", source_doc_id=uuid.uuid4(),
        operation="book_expense", cost_center_id=CC, account_id=acct.id,
        fiscal_year=FY, month=1, amount=Decimal("40"),
    ))
    db.add(BudgetLedger(
        source_service="manual", source_doc_type="opening_balance", source_doc_id=uuid.uuid4(),
        operation="opening", cost_center_id=CC, account_id=acct.id,
        fiscal_year=FY, month=2, amount=Decimal("15"),
    ))
    await db.flush()

    res = await balance_crud.get_monthly_actuals_summary(db, cost_center_id=CC, fiscal_year=FY)
    assert len(res.accounts) == 1
    row = res.accounts[0]
    assert row.plan_by_month[1] == Decimal("100")
    assert row.plan_by_month[2] == Decimal("200")
    assert row.plan_by_month[3] == Decimal("0")
    assert row.actual_by_month[1] == Decimal("40")
    assert row.actual_by_month[2] == Decimal("15")  # opening counts
    assert row.plan_year == Decimal("300")
    assert row.actual_year == Decimal("55")


async def test_monthly_summary_all_cost_centers(db_session):
    db = db_session
    _l1, acct = await _seed_catalog(db)
    await _seed_approved_plan(db, acct, month_amounts={1: Decimal("100")})
    res = await balance_crud.get_monthly_actuals_summary(db, cost_center_id=None, fiscal_year=FY)
    assert res.accounts[0].plan_by_month[1] == Decimal("100")


# ── Req2 ──────────────────────────────────────────────────────────────────────

async def test_import_decomposed_account_clears_breakdowns(db_session):
    db = db_session
    _l1, acct = await _seed_catalog(db, decomposed=True)
    plan = BudgetPlan(
        cost_center_id=CC, fiscal_year=FY, status="draft",
        is_current=True, version=1, created_by=uuid.uuid4(),
    )
    db.add(plan)
    await db.flush()
    # Jan line built from two factor breakdowns summing to 70.
    line = BudgetPlanLine(plan_id=plan.id, account_id=acct.id, month=1, amount=Decimal("70"))
    db.add(line)
    await db.flush()
    db.add(BudgetPlanBreakdown(plan_line_id=line.id, factor_combo={"brand": "A"}, amount=Decimal("30")))
    db.add(BudgetPlanBreakdown(plan_line_id=line.id, factor_combo={"brand": "B"}, amount=Decimal("40")))
    await db.flush()

    csv_text = (
        "L1 Code,L1 Name,Account Code,Account Name,Decomposed,"
        "Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec,Q1,Q2,Q3,Q4,Year\n"
        "L1,Marketing,ACC1,Ads,Yes,500,,,,,,,,,,,,,,,,\n"
    )
    result = await plan_crud.import_plan_csv(db, plan, csv_text)
    assert result.breakdowns_cleared == 2
    assert result.lines_updated == 1

    # Line amount overwritten to the imported total; breakdowns gone.
    refreshed = await plan_crud.get_plan_line(db, plan.id, acct.id, 1)
    assert refreshed.amount == Decimal("500")
    remaining = await plan_crud.list_breakdowns(db, line.id)
    assert remaining == []


async def test_import_blank_cell_leaves_decomposed_breakdowns(db_session):
    db = db_session
    _l1, acct = await _seed_catalog(db, decomposed=True)
    plan = BudgetPlan(
        cost_center_id=CC, fiscal_year=FY, status="draft",
        is_current=True, version=1, created_by=uuid.uuid4(),
    )
    db.add(plan)
    await db.flush()
    line = BudgetPlanLine(plan_id=plan.id, account_id=acct.id, month=1, amount=Decimal("70"))
    db.add(line)
    await db.flush()
    db.add(BudgetPlanBreakdown(plan_line_id=line.id, factor_combo={"brand": "A"}, amount=Decimal("70")))
    await db.flush()

    # Jan blank → no change; breakdowns must remain intact.
    csv_text = (
        "Account Code,Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec\n"
        "ACC1,,,,,,,,,,,,\n"
    )
    result = await plan_crud.import_plan_csv(db, plan, csv_text)
    assert result.breakdowns_cleared == 0
    remaining = await plan_crud.list_breakdowns(db, line.id)
    assert len(remaining) == 1


# ── Req3 ──────────────────────────────────────────────────────────────────────

OPENING_CSV = (
    "Account Code,Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec\n"
    "ACC1,1000,,500,,,,,,,,,\n"
)


async def test_opening_import_counts_as_actual_and_upserts(db_session):
    db = db_session
    _l1, acct = await _seed_catalog(db)

    r1 = await opening_crud.import_opening_csv(db, cost_center_id=CC, fiscal_year=FY, csv_text=OPENING_CSV)
    assert r1.rows_created == 2  # Jan + Mar
    assert r1.rows_updated == 0

    # Opening counts toward actual_spent.
    actual = await get_actual_spent(db, CC, acct.id, FY)
    assert actual == Decimal("1500")

    # Re-import with a changed Jan value → update, not duplicate insert.
    csv2 = (
        "Account Code,Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec\n"
        "ACC1,1200,,500,,,,,,,,,\n"
    )
    r2 = await opening_crud.import_opening_csv(db, cost_center_id=CC, fiscal_year=FY, csv_text=csv2)
    assert r2.rows_created == 0
    assert r2.rows_updated == 1  # only Jan changed

    rows = (await db.execute(
        BudgetLedger.__table__.select().where(BudgetLedger.operation == "opening")
    )).all()
    assert len(rows) == 2  # still two opening rows, no duplicates
    actual2 = await get_actual_spent(db, CC, acct.id, FY)
    assert actual2 == Decimal("1700")


async def test_opening_list_returns_rows(db_session):
    db = db_session
    _l1, acct = await _seed_catalog(db)
    await opening_crud.import_opening_csv(db, cost_center_id=CC, fiscal_year=FY, csv_text=OPENING_CSV)
    listing = await opening_crud.list_opening_balances(db, cost_center_id=CC, fiscal_year=FY)
    assert {r.month for r in listing.items} == {1, 3}
    assert all(r.account_code == "ACC1" for r in listing.items)
