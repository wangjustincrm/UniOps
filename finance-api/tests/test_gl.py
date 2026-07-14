"""General Ledger over POSTED journal vouchers (Plan 5 switchover): trial
balance, statements, account ledger, opening balances, year-end close.

Periods are DYNAMIC (emit_event stamps fiscal_period with today's month) —
hardcoding a month rots the suite at month rollover (the pre-Plan-5 failure).
"""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app

NOW = datetime.now(timezone.utc)
PERIOD = NOW.strftime("%Y-%m")          # current month — where emits land
YEAR = NOW.year


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


async def _post_ar_invoice(client, amount="1000.00", tax="130.00", tax_code="HST_ON"):
    d = NOW.date().isoformat()
    body = {"customer_id": str(uuid.uuid4()), "customer_name": "Loblaw Inc",
            "invoice_date": d, "due_date": d, "currency": "CAD",
            "amount": amount, "tax_lines": [{"tax_code": tax_code, "tax_amount": tax}]}
    r = await client.post("/finance/v1/ar/invoices", headers=_h(), json=body)
    inv_id = r.json()["id"]
    await client.post(f"/finance/v1/ar/invoices/{inv_id}/post", headers=_h())
    return inv_id


async def _post_all_draft_jvs(db_session):
    """Business-event JVs are born draft; GL only sees POSTED. Tests use the
    (idempotent) cut-over backfill to post them without the human flow."""
    from app.crud.journal_voucher import backfill_posted_jvs
    await backfill_posted_jvs(db_session)
    await db_session.flush()


# ── opening balances(逻辑不变,原样保留) ────────────────────────────────────────

async def test_opening_balance_must_balance(client):
    r = await client.post("/finance/v1/gl/opening-balance", headers=_h(), json={
        "as_of": f"{YEAR}-01-01",
        "lines": [{"account_code": "1010", "debit": "100.00"},
                  {"account_code": "3100", "credit": "90.00"}]})
    assert r.status_code == 409 and "unbalanced" in r.json()["detail"].lower()


async def test_opening_balance_posts_and_is_idempotent(client):
    body = {"as_of": f"{YEAR}-01-01",
            "lines": [{"account_code": "1010", "debit": "50000.00"},
                      {"account_code": "3100", "credit": "50000.00"}]}
    r1 = await client.post("/finance/v1/gl/opening-balance", headers=_h(), json=body)
    r2 = await client.post("/finance/v1/gl/opening-balance", headers=_h(), json=body)
    assert r1.json()["posting_event_id"] is not None
    assert r2.json()["already_posted"] is True


async def test_opening_unknown_account_rejected(client):
    r = await client.post("/finance/v1/gl/opening-balance", headers=_h(), json={
        "as_of": f"{YEAR}-01-01",
        "lines": [{"account_code": "9999", "debit": "1.00"},
                  {"account_code": "3100", "credit": "1.00"}]})
    assert r.status_code == 409 and "unknown account" in r.json()["detail"].lower()


# ── draft 不进 GL(切换的核心语义) ────────────────────────────────────────────────

async def test_draft_jv_excluded_until_posted(client, db_session):
    await _post_ar_invoice(client)          # emits → draft JV
    r = await client.get(f"/finance/v1/gl/trial-balance?period={PERIOD}", headers=_h())
    assert r.json()["totals"]["period_debit"] == "0.00"   # draft invisible
    await _post_all_draft_jvs(db_session)
    r2 = await client.get(f"/finance/v1/gl/trial-balance?period={PERIOD}", headers=_h())
    assert r2.json()["totals"]["period_debit"] == "1130.00"


# ── trial balance ─────────────────────────────────────────────────────────────────

async def test_trial_balance_balances(client, db_session):
    await _post_ar_invoice(client)  # DR AR 1130 / CR rev 1000 / CR output tax 130
    await _post_all_draft_jvs(db_session)
    r = await client.get(f"/finance/v1/gl/trial-balance?period={PERIOD}", headers=_h())
    assert r.status_code == 200
    tb = r.json()
    assert tb["balanced"] is True
    assert tb["totals"]["period_debit"] == tb["totals"]["period_credit"] == "1130.00"
    rows = {row["account_code"]: row for row in tb["rows"]}
    assert rows["1100"]["closing"] == "1130.00"
    assert rows["4000"]["closing"] == "-1000.00"
    assert rows["2200"]["closing"] == "-130.00"


async def test_opening_carries_into_next_period_opening_column(client):
    # opening JV auto-posts (Task 1) — no backfill needed
    await client.post("/finance/v1/gl/opening-balance", headers=_h(), json={
        "as_of": f"{YEAR}-01-01",
        "lines": [{"account_code": "1010", "debit": "9000.00"},
                  {"account_code": "3100", "credit": "9000.00"}]})
    later = f"{YEAR}-12"                    # any period after January
    r = await client.get(f"/finance/v1/gl/trial-balance?period={later}", headers=_h())
    rows = {row["account_code"]: row for row in r.json()["rows"]}
    assert rows["1010"]["opening"] == "9000.00"


# ── parity: posting-spine sums == JV-based trial balance(切换零差异证明) ──────────

async def test_parity_posting_spine_vs_jv_trial_balance(client, db_session):
    from sqlalchemy import func, select
    from app.models.posting import PostingEvent, PostingLine
    await _post_ar_invoice(client, amount="777.00", tax="101.01")
    await _post_all_draft_jvs(db_session)
    r = await client.get(f"/finance/v1/gl/trial-balance?period={PERIOD}", headers=_h())
    jv_rows = {row["account_code"]: Decimal(row["closing"]) for row in r.json()["rows"]}
    spine = (await db_session.execute(
        select(PostingLine.account_code,
               func.coalesce(func.sum(PostingLine.debit - PostingLine.credit), 0))
        .join(PostingEvent, PostingLine.event_id == PostingEvent.id)
        .where(PostingEvent.fiscal_period <= PERIOD)
        .group_by(PostingLine.account_code))).all()
    for code, net in spine:
        assert jv_rows.get(code, Decimal("0")) == Decimal(net), f"parity break at {code}"


# ── account ledger ────────────────────────────────────────────────────────────────

async def test_account_ledger_running_balance(client, db_session):
    await _post_ar_invoice(client, amount="1000.00", tax="130.00")
    await _post_all_draft_jvs(db_session)
    r = await client.get(f"/finance/v1/gl/account/1100?period={PERIOD}", headers=_h())
    led = r.json()
    assert led["opening"] == "0.00"
    assert led["closing"] == "1130.00"
    assert led["entries"][-1]["balance"] == "1130.00"


async def test_reports_require_valid_period(client):
    r = await client.get("/finance/v1/gl/trial-balance?period=2026-13", headers=_h())
    assert r.status_code == 422


# ── financial statements(期间动态 + 事件过账;Task 3 切 _balances_through 后仍绿) ──

async def test_income_statement(client, db_session):
    await _post_ar_invoice(client, amount="1000.00", tax="0.00", tax_code="ZERO")
    await _post_all_draft_jvs(db_session)
    r = await client.get(f"/finance/v1/gl/income-statement?period={PERIOD}&ytd=true",
                         headers=_h())
    s = r.json()
    assert s["revenue_total"] == "1000.00"
    assert s["net_income"] == "1000.00"
    assert any(row["account_code"] == "4000" for row in s["revenue"])


async def test_balance_sheet_balances_with_net_income(client, db_session):
    await _post_ar_invoice(client, amount="1000.00", tax="130.00")
    await _post_all_draft_jvs(db_session)
    r = await client.get(f"/finance/v1/gl/balance-sheet?period={PERIOD}", headers=_h())
    bs = r.json()
    assert bs["balanced"] is True
    assert bs["assets_total"] == bs["liabilities_and_equity_total"]
    assert any("Current Year Earnings" in e["account_name"] for e in bs["equity"])


# ── journal(posted JV 凭证列表) ──────────────────────────────────────────────────

async def test_journal_lists_posted_jvs(client, db_session):
    await _post_ar_invoice(client)
    r0 = await client.get(f"/finance/v1/gl/journal?period={PERIOD}", headers=_h())
    assert r0.json() == []                          # draft not in the journal
    await _post_all_draft_jvs(db_session)
    r = await client.get(f"/finance/v1/gl/journal?period={PERIOD}", headers=_h())
    entries = r.json()
    assert len(entries) == 1
    e = entries[0]
    assert e["jv_number"].startswith("JV-")
    assert e["source"] == "ar_invoice:" + e["source"].split(":", 1)[1]
    assert len(e["lines"]) == 3                     # AR / revenue / output tax
    assert {l["account_code"] for l in e["lines"]} == {"1100", "4000", "2200"}


# ── year-end close ────────────────────────────────────────────────────────────────

async def test_close_year_sweeps_pl_to_retained_earnings(client, db_session):
    await _post_ar_invoice(client, amount="1000.00", tax="0.00", tax_code="ZERO")
    await _post_all_draft_jvs(db_session)
    r = await client.post("/finance/v1/gl/close-year", headers=_h(),
                          json={"fiscal_year": YEAR})
    assert r.status_code == 200, r.text
    assert r.json()["net_income"] == "1000.00"

    # P&L zeroed in the YTD income statement after the close period (Dec);
    # the closing JV auto-posts (Task 1) — no backfill needed for it
    r = await client.get(f"/finance/v1/gl/income-statement?period={YEAR}-12&ytd=true",
                         headers=_h())
    assert r.json()["net_income"] == "0.00"

    r = await client.get(f"/finance/v1/gl/account/3100?period={YEAR}-12", headers=_h())
    assert r.json()["closing"] == "-1000.00"

    r2 = await client.post("/finance/v1/gl/close-year", headers=_h(),
                           json={"fiscal_year": YEAR})
    assert r2.json()["already_closed"] is True
