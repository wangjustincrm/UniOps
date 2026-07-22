import uuid
import pytest
from app.crud import balance as balance_crud


@pytest.mark.asyncio
async def test_summary_empty_cc_ids_returns_no_accounts(db_session, seed_two_cc_plans):
    # seed_two_cc_plans: fixture creating approved plans in CC-A and CC-B, FY 2026.
    res = await balance_crud.get_actuals_summary(
        db_session, fiscal_year=2026, cc_ids=[])
    assert res.accounts == []


@pytest.mark.asyncio
async def test_summary_cc_ids_restricts_aggregate(db_session, seed_two_cc_plans):
    cc_a = seed_two_cc_plans["cc_a"]
    only_a = await balance_crud.get_actuals_summary(
        db_session, fiscal_year=2026, cc_ids=[cc_a])
    all_cc = await balance_crud.get_actuals_summary(db_session, fiscal_year=2026)
    a_total = sum(x.annual_budget for x in only_a.accounts)
    all_total = sum(x.annual_budget for x in all_cc.accounts)
    assert a_total < all_total  # A-only excludes CC-B's plan
