"""Budget Dashboard dept-scoping — Task 5: `cc_ids` filter on the four NC-actuals
CRUD functions (account_balance.py). Task 6: the `/gl` endpoints wire that
scope in via `_cc_scope` (account_balance.py). `seed_posted_jv_two_cc` fixture
lives in conftest.py."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.crud import account_balance as crud
from app.db.base import get_db
from app.main import app


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


# ── Task 6: /gl endpoint scoping ────────────────────────────────────────────
# `client` fixture pattern copied from test_account_balance.py:67 (dependency
# override -> the test's own db_session, never the app's own engine). Token
# signer copied from test_jv_api.py:19.

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
async def admin_token():
    """Full-access caller — 'finance_manager' is in budget_scope.FULL_ACCESS_PRIMARY."""
    return _token("finance_manager", uuid.uuid4())


@pytest_asyncio.fixture
async def dept_manager_token(seed_posted_jv_two_cc):
    """A caller in dept_a (owns cc_a only) — 'dept_manager' is NOT a full-access
    role, and seed_posted_jv_two_cc seeded `mgr_uid`'s users row in dept_a."""
    return _token("dept_manager", seed_posted_jv_two_cc["mgr_uid"])


async def test_nc_actuals_endpoint_scopes_to_department(
        client, dept_manager_token, admin_token, seed_posted_jv_two_cc):
    r = await client.get("/finance/v1/gl/nc-actuals-monthly?fiscal_year=2026",
                         headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200, r.text  # never 403

    def total(body):
        return sum(sum(float(v) for v in m.values()) for m in body["accounts"].values())

    r_all = await client.get("/finance/v1/gl/nc-actuals-monthly?fiscal_year=2026",
                             headers={"Authorization": f"Bearer {admin_token}"})
    assert r_all.status_code == 200, r_all.text
    assert total(r.json()) <= total(r_all.json())
    # dept viewer must NOT see the other department's CC total (100 vs 140)
    assert total(r.json()) < total(r_all.json())
    assert total(r.json()) == 100.00
    assert total(r_all.json()) == 140.00


async def test_nc_actuals_endpoint_full_access_unchanged_with_requested_cc(
        client, admin_token, seed_posted_jv_two_cc):
    """Full-access caller explicitly requesting one cost center still gets the
    exact old single-CC behaviour (no dept clamp applied)."""
    cc_b = seed_posted_jv_two_cc["cc_b"]
    r = await client.get(
        f"/finance/v1/gl/nc-actuals-monthly?fiscal_year=2026&cost_center_id={cc_b}",
        headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    item = str(seed_posted_jv_two_cc["income_expense_item_id"])
    assert r.json()["accounts"][item] == {"6": "40.00"}


async def test_nc_partner_monthly_endpoint_scopes_to_department(
        client, dept_manager_token, admin_token, seed_posted_jv_two_cc):
    item = seed_posted_jv_two_cc["income_expense_item_id"]
    r = await client.get(
        f"/finance/v1/gl/nc-partner-monthly?income_expense_item_id={item}&fiscal_year=2026",
        headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200, r.text
    total_dept = sum(float(p["year_total"]) for p in r.json()["partners"])

    r_all = await client.get(
        f"/finance/v1/gl/nc-partner-monthly?income_expense_item_id={item}&fiscal_year=2026",
        headers={"Authorization": f"Bearer {admin_token}"})
    total_all = sum(float(p["year_total"]) for p in r_all.json()["partners"])
    assert total_dept == 100.00
    assert total_all == 140.00


async def test_nc_partner_vouchers_endpoint_scopes_to_department(
        client, dept_manager_token, seed_posted_jv_two_cc):
    item = seed_posted_jv_two_cc["income_expense_item_id"]
    cc_b = seed_posted_jv_two_cc["cc_b"]
    # dept-manager (dept_a) explicitly requesting cc_b (out of scope) must be
    # clamped to their own department, not 403'd and not shown cc_b's rows.
    r = await client.get(
        f"/finance/v1/gl/nc-partner-vouchers?income_expense_item_id={item}"
        f"&fiscal_year=2026&month=6&cost_center_id={cc_b}",
        headers={"Authorization": f"Bearer {dept_manager_token}"})
    assert r.status_code == 200, r.text
    assert all(row["local_debit"] != "40.00" for row in r.json()["rows"])


async def test_budget_actual_partner_export_endpoint_smoke(
        client, dept_manager_token, admin_token, seed_posted_jv_two_cc):
    """Smoke test — the export endpoint must not 403/500 for a scoped caller and
    must still work for a full-access caller. budget-api is not running in this
    test process, so the plan side fails open to plan=0 (mirrors
    /budget-actual-grid's existing fail-open behaviour)."""
    for tok in (dept_manager_token, admin_token):
        r = await client.get(
            "/finance/v1/gl/budget-actual/partner-export?fiscal_year=2026",
            headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
