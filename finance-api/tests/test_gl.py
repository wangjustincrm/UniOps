"""General Ledger closed loop (Phase f): trial balance, statements, account
ledger, opening balances, and year-end close to retained earnings."""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app


def _h(role="finance_manager"):
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _post_ar_invoice(client, amount="1000.00", tax="130.00", tax_code="HST_ON",
                           invoice_date="2026-06-03", due_date="2026-07-03"):
    body = {"customer_id": str(uuid.uuid4()), "customer_name": "Loblaw Inc",
            "invoice_date": invoice_date, "due_date": due_date, "currency": "CAD",
            "amount": amount, "tax_lines": [{"tax_code": tax_code, "tax_amount": tax}]}
    r = await client.post("/finance/v1/ar/invoices", headers=_h(), json=body)
    inv_id = r.json()["id"]
    await client.post(f"/finance/v1/ar/invoices/{inv_id}/post", headers=_h())
    return inv_id


# ── opening balances ──────────────────────────────────────────────────────────────

async def test_opening_balance_must_balance(client):
    r = await client.post("/finance/v1/gl/opening-balance", headers=_h(), json={
        "as_of": "2026-01-01",
        "lines": [{"account_code": "1010", "debit": "100.00"},
                  {"account_code": "3100", "credit": "90.00"}]})  # unbalanced
    assert r.status_code == 409 and "unbalanced" in r.json()["detail"].lower()


async def test_opening_balance_posts_and_is_idempotent(client):
    body = {"as_of": "2026-01-01",
            "lines": [{"account_code": "1010", "debit": "50000.00"},
                      {"account_code": "3100", "credit": "50000.00"}]}
    r1 = await client.post("/finance/v1/gl/opening-balance", headers=_h(), json=body)
    r2 = await client.post("/finance/v1/gl/opening-balance", headers=_h(), json=body)
    assert r1.json()["posting_event_id"] is not None
    assert r2.json()["already_posted"] is True


async def test_opening_unknown_account_rejected(client):
    r = await client.post("/finance/v1/gl/opening-balance", headers=_h(), json={
        "as_of": "2026-01-01",
        "lines": [{"account_code": "9999", "debit": "1.00"},
                  {"account_code": "3100", "credit": "1.00"}]})
    assert r.status_code == 409 and "unknown account" in r.json()["detail"].lower()


# ── trial balance ─────────────────────────────────────────────────────────────────

async def test_trial_balance_balances(client):
    await _post_ar_invoice(client)  # DR AR 1130 / CR rev 1000 / CR output tax 130
    r = await client.get("/finance/v1/gl/trial-balance?period=2026-06", headers=_h())
    assert r.status_code == 200
    tb = r.json()
    assert tb["balanced"] is True
    assert tb["totals"]["period_debit"] == tb["totals"]["period_credit"] == "1130.00"
    rows = {row["account_code"]: row for row in tb["rows"]}
    assert rows["1100"]["closing"] == "1130.00"     # AR debit
    assert rows["4000"]["closing"] == "-1000.00"    # revenue credit
    assert rows["2200"]["closing"] == "-130.00"     # GST/HST payable credit


async def test_opening_carries_into_next_period_opening_column(client):
    await client.post("/finance/v1/gl/opening-balance", headers=_h(), json={
        "as_of": "2026-05-31",
        "lines": [{"account_code": "1010", "debit": "9000.00"},
                  {"account_code": "3100", "credit": "9000.00"}]})
    r = await client.get("/finance/v1/gl/trial-balance?period=2026-06", headers=_h())
    rows = {row["account_code"]: row for row in r.json()["rows"]}
    assert rows["1010"]["opening"] == "9000.00"     # prior-period balance as opening


# ── financial statements ──────────────────────────────────────────────────────────

async def test_income_statement(client):
    await _post_ar_invoice(client, amount="1000.00", tax="0.00", tax_code="ZERO",
                           invoice_date="2026-06-03")
    r = await client.get("/finance/v1/gl/income-statement?period=2026-06&ytd=true", headers=_h())
    s = r.json()
    assert s["revenue_total"] == "1000.00"
    assert s["net_income"] == "1000.00"
    assert any(row["account_code"] == "4000" for row in s["revenue"])


async def test_balance_sheet_balances_with_net_income(client):
    # revenue raises AR (asset) and revenue (→ equity via net income); BS must balance
    await _post_ar_invoice(client, amount="1000.00", tax="130.00")
    r = await client.get("/finance/v1/gl/balance-sheet?period=2026-06", headers=_h())
    bs = r.json()
    assert bs["balanced"] is True
    assert bs["assets_total"] == bs["liabilities_and_equity_total"]
    # net income shows up in equity
    assert any("Current Year Earnings" in e["account_name"] for e in bs["equity"])


# ── account ledger ────────────────────────────────────────────────────────────────

async def test_account_ledger_running_balance(client):
    await _post_ar_invoice(client, amount="1000.00", tax="130.00")
    r = await client.get("/finance/v1/gl/account/1100?period=2026-06", headers=_h())
    led = r.json()
    assert led["opening"] == "0.00"
    assert led["closing"] == "1130.00"
    assert led["entries"][-1]["balance"] == "1130.00"


# ── year-end close (the loop) ─────────────────────────────────────────────────────

async def test_close_year_sweeps_pl_to_retained_earnings(client):
    # revenue 1000 (no tax to keep it clean), period in 2026
    await _post_ar_invoice(client, amount="1000.00", tax="0.00", tax_code="ZERO",
                           invoice_date="2026-06-03")
    r = await client.post("/finance/v1/gl/close-year", headers=_h(), json={"fiscal_year": 2026})
    assert r.status_code == 200, r.text
    assert r.json()["net_income"] == "1000.00"

    # P&L is zeroed in the YTD income statement after the close period (Dec)
    r = await client.get("/finance/v1/gl/income-statement?period=2026-12&ytd=true", headers=_h())
    assert r.json()["net_income"] == "0.00"

    # retained earnings now carries the net income (credit)
    r = await client.get("/finance/v1/gl/account/3100?period=2026-12", headers=_h())
    assert r.json()["closing"] == "-1000.00"

    # idempotent
    r2 = await client.post("/finance/v1/gl/close-year", headers=_h(), json={"fiscal_year": 2026})
    assert r2.json()["already_closed"] is True


async def test_reports_require_valid_period(client):
    r = await client.get("/finance/v1/gl/trial-balance?period=2026-13", headers=_h())
    assert r.status_code == 422
