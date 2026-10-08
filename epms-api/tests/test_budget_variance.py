"""Budget against spending: the answer a query could not give.

Someone asked for each cost centre's annual budget, the actual through August,
the variance and the variance percentage. The assistant returned the budgets,
said it could not find the actuals, and when told to go and get them returned
the budgets again. Nothing had failed — the query layer answers from one entity
and the model may not subtract, so that question had no path through it.

What is asserted here is that the path it has now cannot go wrong in the three
ways that matter: the arithmetic, the scope, and what happens when half the
answer is missing. A plan presented alone, as though it answered a question
about variance, is the specific failure being prevented.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ontology import get_entity
from app.models.department import Department
from app.services import budget_variance

pytestmark = pytest.mark.asyncio

FULL = {"perms": {"finance.budget.view_all": True}, "user_id": None}
_DEPT_CODE = "VAR-TEST-DEPT"
NONE_PERMS = {"perms": {}, "user_id": None}


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db(test_engine):
    """A session, and removal of only the rows this file writes."""
    async with _factory(test_engine)() as session:
        yield session
    plans = get_entity("budget_plan").model
    lines = get_entity("budget_plan_line").model
    cc = get_entity("cost_center").model
    async with _factory(test_engine)() as cleanup:
        mine = sa.select(plans.id).where(plans.fiscal_year == 2999)
        await cleanup.execute(sa.delete(lines).where(lines.plan_id.in_(mine)))
        await cleanup.execute(sa.delete(plans).where(plans.fiscal_year == 2999))
        await cleanup.execute(sa.delete(cc).where(cc.code.like("VAR-TEST-%")))
        await cleanup.execute(
            sa.delete(Department).where(Department.code == _DEPT_CODE))
        await cleanup.commit()


async def _plan(db, code: str, monthly: dict[int, str], *, status="approved",
                is_current=True):
    """One cost centre with one approved plan for fiscal year 2999."""
    cc = get_entity("cost_center").model
    plans = get_entity("budget_plan").model
    lines = get_entity("budget_plan_line").model
    cc_id, plan_id = uuid.uuid4(), uuid.uuid4()
    await db.execute(sa.insert(cc).values(
        id=cc_id, code=f"VAR-TEST-{code}", name=f"Variance test {code}",
        department_id=await _department(db), is_active=True))
    await db.execute(sa.insert(plans).values(
        id=plan_id, cost_center_id=cc_id, fiscal_year=2999, status=status,
        is_current=is_current, version=1))
    for month, amount in monthly.items():
        await db.execute(sa.insert(lines).values(
            id=uuid.uuid4(), plan_id=plan_id, account_id=None, month=month,
            amount=Decimal(amount)))
    await db.commit()
    return str(cc_id)


def _actual(rows, dropped=None):
    """Stand in for finance-api, whose own tests cover what it counts."""
    async def _call(*, bearer_token=None, fiscal_year, through_month):
        return {"fiscal_year": fiscal_year, "through_month": through_month,
                "cost_centers": rows,
                "dropped": dropped or {},
                "dropped_means": "..." if dropped else ""}
    return _call


async def _department(db) -> uuid.UUID:
    """cost_centers.department_id is NOT NULL and a real FK — a cost centre
    cannot exist without one, so the fixture makes one rather than passing a
    uuid that would fail at insert."""
    existing = (await db.execute(
        sa.select(Department.id).where(Department.code == _DEPT_CODE))).scalar()
    if existing:
        return existing
    dept_id = uuid.uuid4()
    await db.execute(sa.insert(Department).values(
        id=dept_id, code=_DEPT_CODE, name="Variance test dept", is_active=True))
    return dept_id


def _row(out, code):
    return next(r for r in out["rows"] if r["cost_center_code"] == f"VAR-TEST-{code}")


async def test_the_subtraction_is_done_and_both_comparisons_are_given(db, monkeypatch):
    cc_id = await _plan(db, "A", {m: "100.00" for m in range(1, 13)})
    monkeypatch.setattr(budget_variance.finance_client, "nc_actuals_by_cost_center",
                        _actual([{"cost_center_id": cc_id, "cost_center_code": "VAR-TEST-A",
                                  "cost_center_name": "Variance test A", "actual": "500.00"}]))
    out = await budget_variance.build(db, FULL, fiscal_year=2999, through_month=8,
                                      token="t")
    row = _row(out, "A")
    assert row["annual_plan"] == "1200.00"      # the whole year
    assert row["plan_to_date"] == "800.00"      # January to August of it
    assert row["actual_to_date"] == "500.00"
    # Plan minus actual: positive is under budget, on both bases.
    assert row["variance_vs_annual"] == "700.00"
    assert row["variance_pct_vs_annual"] == "58.3"
    assert row["variance_vs_plan_to_date"] == "300.00"
    assert row["variance_pct_vs_plan_to_date"] == "37.5"


async def test_spending_with_no_approved_plan_is_not_under_budget(db, monkeypatch):
    """The row that would otherwise read as the best-performing cost centre."""
    cc_id = str(uuid.uuid4())
    monkeypatch.setattr(budget_variance.finance_client, "nc_actuals_by_cost_center",
                        _actual([{"cost_center_id": cc_id, "cost_center_code": "VAR-TEST-Z",
                                  "cost_center_name": "No plan", "actual": "900.00"}]))
    out = await budget_variance.build(db, FULL, fiscal_year=2999, through_month=12,
                                      token="t")
    row = _row(out, "Z")
    assert row["no_approved_plan"] is True
    assert row["annual_plan"] == "0.00"
    # A percentage against a budget of nothing would sort to the top of the very
    # list someone scans for their worst overspend.
    assert row["variance_pct_vs_annual"] is None


async def test_a_draft_plan_is_not_a_budget(db, monkeypatch):
    await _plan(db, "D", {1: "50.00"}, status="draft")
    monkeypatch.setattr(budget_variance.finance_client, "nc_actuals_by_cost_center",
                        _actual([]))
    out = await budget_variance.build(db, FULL, fiscal_year=2999, through_month=12,
                                      token="t")
    assert not [r for r in out["rows"] if r["cost_center_code"] == "VAR-TEST-D"]


async def test_a_superseded_version_does_not_add_to_the_current_one(db, monkeypatch):
    await _plan(db, "S", {1: "10.00"}, is_current=False)
    await _plan(db, "S2", {1: "10.00"})
    monkeypatch.setattr(budget_variance.finance_client, "nc_actuals_by_cost_center",
                        _actual([]))
    out = await budget_variance.build(db, FULL, fiscal_year=2999, through_month=12,
                                      token="t")
    assert _row(out, "S2")["annual_plan"] == "10.00"
    assert not [r for r in out["rows"] if r["cost_center_code"] == "VAR-TEST-S"]


async def test_without_budget_access_it_refuses_rather_than_computing(db):
    out = await budget_variance.build(db, NONE_PERMS, fiscal_year=2999,
                                      through_month=12, token="t")
    assert out["allowed"] is False and out["why"]
    assert "rows" not in out


async def test_an_unreachable_actual_is_not_answered_with_the_plan(db, monkeypatch):
    """The exact shape of the original failure, made impossible.

    finance-api down must not degrade into "here are the budgets" — that is a
    different question's answer wearing this question's heading.
    """
    await _plan(db, "U", {1: "10.00"})

    async def gone(*, bearer_token=None, fiscal_year, through_month):
        return None

    monkeypatch.setattr(budget_variance.finance_client, "nc_actuals_by_cost_center", gone)
    out = await budget_variance.build(db, FULL, fiscal_year=2999, through_month=12,
                                      token="t")
    assert out["rows"] == []
    assert out["actual_unavailable"]


async def test_what_the_actual_could_not_place_travels_with_the_answer(db, monkeypatch):
    """Spending missing from the actual reads as a cost centre doing well."""
    await _plan(db, "M", {1: "10.00"})
    monkeypatch.setattr(
        budget_variance.finance_client, "nc_actuals_by_cost_center",
        _actual([], dropped={"no_cost_center": {"lines": 927, "amount": "9790000.00"}}))
    out = await budget_variance.build(db, FULL, fiscal_year=2999, through_month=12,
                                      token="t")
    assert out["not_in_the_actual"]["no_cost_center"]["lines"] == 927
    assert out["not_in_the_actual_means"]
