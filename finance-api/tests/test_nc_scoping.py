"""Budget Dashboard dept-scoping — Task 5: `cc_ids` filter on the four NC-actuals
CRUD functions (account_balance.py). `seed_posted_jv_two_cc` fixture lives in
conftest.py."""
from app.crud import account_balance as crud


# ── nc_actuals_monthly ──────────────────────────────────────────────────────

async def test_nc_actuals_empty_cc_ids_is_empty(db_session, seed_posted_jv_two_cc):
    res = await crud.nc_actuals_monthly(db_session, 2026, cc_ids=[])
    assert res == {"fiscal_year": 2026, "accounts": {}}


async def test_nc_actuals_cc_ids_restricts(db_session, seed_posted_jv_two_cc):
    cc_a = seed_posted_jv_two_cc["cc_a"]
    only_a = await crud.nc_actuals_monthly(db_session, 2026, cc_ids=[cc_a])
    all_cc = await crud.nc_actuals_monthly(db_session, 2026)

    item = str(seed_posted_jv_two_cc["income_expense_item_id"])
    assert only_a["accounts"][item] == {6: "100.00"}
    assert all_cc["accounts"][item] == {6: "140.00"}


async def test_nc_actuals_cc_ids_none_matches_unfiltered(db_session, seed_posted_jv_two_cc):
    """cc_ids=None must leave existing (cost_center_id-only) behavior unchanged."""
    baseline = await crud.nc_actuals_monthly(db_session, 2026)
    unset = await crud.nc_actuals_monthly(db_session, 2026, cc_ids=None)
    assert baseline == unset


# ── nc_partner_monthly ──────────────────────────────────────────────────────

async def test_nc_partner_monthly_empty_cc_ids_is_empty(db_session, seed_posted_jv_two_cc):
    item = seed_posted_jv_two_cc["income_expense_item_id"]
    res = await crud.nc_partner_monthly(db_session, item, 2026, cc_ids=[])
    assert res["partners"] == []


async def test_nc_partner_monthly_cc_ids_restricts(db_session, seed_posted_jv_two_cc):
    item = seed_posted_jv_two_cc["income_expense_item_id"]
    cc_a = seed_posted_jv_two_cc["cc_a"]
    only_a = await crud.nc_partner_monthly(db_session, item, 2026, cc_ids=[cc_a])
    all_cc = await crud.nc_partner_monthly(db_session, item, 2026)
    total_a = sum(float(p["year_total"]) for p in only_a["partners"])
    total_all = sum(float(p["year_total"]) for p in all_cc["partners"])
    assert total_a == 100.00
    assert total_all == 140.00


# ── nc_partner_monthly_all ──────────────────────────────────────────────────

async def test_nc_partner_monthly_all_empty_cc_ids_is_empty(db_session, seed_posted_jv_two_cc):
    res = await crud.nc_partner_monthly_all(db_session, fiscal_year=2026, cc_ids=[])
    assert res == {}


async def test_nc_partner_monthly_all_cc_ids_restricts(db_session, seed_posted_jv_two_cc):
    item = str(seed_posted_jv_two_cc["income_expense_item_id"])
    cc_a = seed_posted_jv_two_cc["cc_a"]
    only_a = await crud.nc_partner_monthly_all(db_session, fiscal_year=2026, cc_ids=[cc_a])
    all_cc = await crud.nc_partner_monthly_all(db_session, fiscal_year=2026)
    total_a = sum(float(p["year_total"]) for p in only_a[item])
    total_all = sum(float(p["year_total"]) for p in all_cc[item])
    assert total_a == 100.00
    assert total_all == 140.00


# ── nc_partner_vouchers ──────────────────────────────────────────────────────

async def test_nc_partner_vouchers_empty_cc_ids_is_empty(db_session, seed_posted_jv_two_cc):
    item = seed_posted_jv_two_cc["income_expense_item_id"]
    res = await crud.nc_partner_vouchers(db_session, item, 2026, 6, cc_ids=[])
    assert res == {"period": "2026-06", "rows": []}


async def test_nc_partner_vouchers_cc_ids_restricts(db_session, seed_posted_jv_two_cc):
    item = seed_posted_jv_two_cc["income_expense_item_id"]
    cc_a = seed_posted_jv_two_cc["cc_a"]
    only_a = await crud.nc_partner_vouchers(db_session, item, 2026, 6, cc_ids=[cc_a])
    all_cc = await crud.nc_partner_vouchers(db_session, item, 2026, 6)
    assert len(only_a["rows"]) == 1
    assert only_a["rows"][0]["local_debit"] == "100.00"
    assert len(all_cc["rows"]) == 2
