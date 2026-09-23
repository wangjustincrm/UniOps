"""`/gl/nc-actual-for-budget-check` — the NC figure a PR is judged against.

budget-api's `/balance` calls this so that the over-budget test on a PR and
the NC-actual line on the Budget Dashboard are the same number. Two things
have to hold, and the second is the one with teeth:

  1. It is the dashboard's own figure, narrowed to one account — same CRUD,
     same exclusions, not a lookalike query that can drift from it.
  2. It is NOT clamped to the caller's department, unlike every other NC read
     in this router. Those are reports, where showing less is safe. This one
     is a criterion: clamping it answers 0 for a cost center outside the
     caller's scope, `available` then comes back as the full annual budget,
     and the PR passes. Fail-closed on a report is fail-OPEN on a control.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.crud import account_balance as crud
from app.db.base import get_db
from app.main import app

_URL = "/finance/v1/gl/nc-actual-for-budget-check"


@pytest_asyncio.fixture
async def client(db_session):
    async def _override():
        yield db_session
    app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _token(role: str, sub) -> str:
    return jwt.encode(
        {"sub": str(sub), "role": role, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@pytest_asyncio.fixture
async def dept_manager_token(seed_posted_jv_two_cc):
    """A caller whose department owns cc_a only (see the fixture's docstring)."""
    return _token("dept_manager", seed_posted_jv_two_cc["mgr_uid"])


# ── CRUD: the account_id filter narrows the dashboard query, it doesn't replace it ──

async def test_account_filter_returns_that_account_s_slice_of_the_unfiltered_result(
        db_session, seed_posted_jv_two_cc):
    item = seed_posted_jv_two_cc["income_expense_item_id"]
    cc_a = seed_posted_jv_two_cc["cc_a"]

    unfiltered = await crud.nc_actuals_monthly(db_session, 2026, cc_a)
    filtered = await crud.nc_actuals_monthly(db_session, 2026, cc_a, account_id=item)

    # Identical, not merely similar — one definition of "NC posted actual".
    assert filtered["accounts"] == {str(item): unfiltered["accounts"][str(item)]}
    assert filtered["accounts"][str(item)] == {6: "100.00"}


async def test_account_filter_omitted_leaves_the_dashboard_query_untouched(
        db_session, seed_posted_jv_two_cc):
    """The dashboard calls this positionally and must be unaffected."""
    baseline = await crud.nc_actuals_monthly(db_session, 2026)
    unset = await crud.nc_actuals_monthly(db_session, 2026, account_id=None)
    assert baseline == unset


async def test_account_filter_on_an_account_with_no_postings_is_empty(
        db_session, seed_posted_jv_two_cc):
    res = await crud.nc_actuals_monthly(db_session, 2026, account_id=uuid.uuid4())
    assert res["accounts"] == {}


# ── Endpoint ────────────────────────────────────────────────────────────────

async def test_returns_the_year_total_for_one_cc_and_account(
        client, dept_manager_token, seed_posted_jv_two_cc):
    cc_a = seed_posted_jv_two_cc["cc_a"]
    item = seed_posted_jv_two_cc["income_expense_item_id"]
    r = await client.get(
        f"{_URL}?fiscal_year=2026&cost_center_id={cc_a}&account_id={item}",
        headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["actual"]) == Decimal("100.00")


async def test_out_of_department_returns_the_real_figure_not_zero(
        client, dept_manager_token, seed_posted_jv_two_cc):
    """★ The reason this endpoint exists instead of reusing the report one.

    The caller's department owns cc_a; cc_b belongs to another department and
    holds 40.00. The dashboard endpoint clamps that to nothing for this caller
    (test_nc_scoping.py pins that, and it is correct there). Here it must come
    back as 40.00: a dept_admin raising a PR for another department, or a user
    with no department at all, would otherwise have every PR measured against
    an actual of 0 and pass. Nobody would see it happen — an over-budget PR
    that sails through looks exactly like one that was within budget."""
    cc_b = seed_posted_jv_two_cc["cc_b"]
    item = seed_posted_jv_two_cc["income_expense_item_id"]
    r = await client.get(
        f"{_URL}?fiscal_year=2026&cost_center_id={cc_b}&account_id={item}",
        headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["actual"]) == Decimal("40.00")


async def test_the_report_endpoint_still_clamps_the_same_caller(
        client, dept_manager_token, seed_posted_jv_two_cc):
    """The admission test's other half: the criterion endpoint being unclamped
    must not have loosened the reports. Same token, same fixture — the
    dashboard read still shows this caller only their own department's 100.00,
    never cc_b's 40.00."""
    r = await client.get("/finance/v1/gl/nc-actuals-monthly?fiscal_year=2026",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200, r.text
    total = sum(sum(Decimal(v) for v in months.values())
                for months in r.json()["accounts"].values())
    assert total == Decimal("100.00")


async def test_an_account_with_no_nc_postings_is_zero(
        client, dept_manager_token, seed_posted_jv_two_cc):
    """A real zero — nothing posted — and it must be reported as such. This is
    the one case where 0 is the right answer; budget-api distinguishes it from
    'could not ask' by that being an error, never a number."""
    cc_a = seed_posted_jv_two_cc["cc_a"]
    r = await client.get(
        f"{_URL}?fiscal_year=2026&cost_center_id={cc_a}&account_id={uuid.uuid4()}",
        headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["actual"]) == Decimal("0")


async def test_requires_authentication(client, seed_posted_jv_two_cc):
    cc_a = seed_posted_jv_two_cc["cc_a"]
    item = seed_posted_jv_two_cc["income_expense_item_id"]
    r = await client.get(f"{_URL}?fiscal_year=2026&cost_center_id={cc_a}&account_id={item}")
    assert r.status_code in (401, 403)
