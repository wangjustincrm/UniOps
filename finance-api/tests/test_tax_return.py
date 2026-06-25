"""GST/HST return worksheet + claim sales-tax code split (Phase a A5)."""
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.mirrors import ExpenseClaim, ExpenseLineItem
from app.models.posting import PostingEvent, PostingLine


def _token(role="finance_manager"):
    return jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h(role="finance_manager"):
    return {"Authorization": f"Bearer {_token(role)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _accrual(db, period_date: date, taxes: list[tuple[str | None, str]]):
    """Create an accrual-like event with sales_tax lines in the given period."""
    ev = PostingEvent(
        source_service="finance", source_doc_type="invoice", source_doc_id=uuid.uuid4(),
        source_doc_number=f"INV-{uuid.uuid4().hex[:6]}", event_type="accrual",
        occurred_at=datetime(period_date.year, period_date.month, period_date.day, tzinfo=timezone.utc),
        fiscal_period=period_date.strftime("%Y-%m"),
    )
    db.add(ev)
    await db.flush()
    n = 1
    for code, amt in taxes:
        db.add(PostingLine(event_id=ev.id, line_no=n, line_role="sales_tax",
                           tax_code=code, debit=Decimal(amt)))
        n += 1
    await db.flush()


async def test_gst_hst_return_aggregates_itc_by_code(client, db_session):
    await _accrual(db_session, date(2026, 6, 3), [("GST", "5.00"), ("HST_ON", "13.00")])
    await _accrual(db_session, date(2026, 6, 20), [("GST", "2.50"), (None, "7.00")])
    await _accrual(db_session, date(2026, 7, 1), [("GST", "99.00")])  # different period

    r = await client.get("/finance/v1/tax/gst-hst-return", headers=_h(),
                         params={"period": "2026-06"})
    assert r.status_code == 200, r.text
    b = r.json()
    itc = {row["tax_code"]: row["itc"] for row in b["itc_by_code"]}
    assert itc["GST"] == "7.50" and itc["HST_ON"] == "13.00"
    assert b["uncoded_itc"] == "7.00"
    assert b["itc_total"] == "27.50"
    assert b["output_tax_total"] == "0"
    assert b["net_tax"] == "-27.50"   # all input, refund/credit


async def test_export_csv(client, db_session):
    await _accrual(db_session, date(2026, 5, 5), [("HST_ON", "13.00")])
    r = await client.get("/finance/v1/tax/gst-hst-return/export", headers=_h(),
                         params={"period": "2026-05"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "HST_ON" in r.text and "Total ITC" in r.text


async def test_bad_period_422(client):
    r = await client.get("/finance/v1/tax/gst-hst-return", headers=_h(),
                         params={"period": "2026-13"})
    assert r.status_code == 422


# ── claim sales-tax now splits by line tax_code (A5 feeds the return) ────────────

async def test_claim_payment_splits_sales_tax_by_code(client, db_session):
    claim = ExpenseClaim(
        claim_number=f"EXP-{uuid.uuid4().hex[:8]}", claim_type="EXP", status="approved",
        employee_name="Jane Doe", currency="CAD",
        total_amount=Decimal("118.00"), tax_amount=Decimal("18.00"),
        net_amount=Decimal("100.00"))
    db_session.add(claim)
    await db_session.flush()
    cc, acct = uuid.uuid4(), uuid.uuid4()
    db_session.add_all([
        ExpenseLineItem(claim_id=claim.id, budget_account_id=acct, cost_center_id=cc,
                        net_amount=Decimal("60.00"), tax_amount=Decimal("13.00"), tax_code="HST_ON"),
        ExpenseLineItem(claim_id=claim.id, budget_account_id=acct, cost_center_id=cc,
                        net_amount=Decimal("40.00"), tax_amount=Decimal("5.00"), tax_code="GST"),
    ])
    await db_session.flush()

    r = await client.post("/finance/v1/payments/execute", headers=_h("ap_clerk"),
                          json={"doc_kind": "expense_claim", "doc_id": str(claim.id),
                                "payment_date": "2026-06-10"})
    assert r.status_code == 200, r.text

    ev = (await db_session.execute(
        select(PostingEvent).where(PostingEvent.source_doc_id == claim.id))).scalar_one()
    tax = {l.tax_code: l.debit for l in (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev.id,
                                  PostingLine.line_role == "sales_tax"))).scalars().all()}
    assert tax == {"HST_ON": Decimal("13.00"), "GST": Decimal("5.00")}

    # and it shows up coded in the return
    r = await client.get("/finance/v1/tax/gst-hst-return", headers=_h(),
                         params={"period": "2026-06"})
    itc = {row["tax_code"]: row["itc"] for row in r.json()["itc_by_code"]}
    assert itc.get("HST_ON") == "13.00" and itc.get("GST") == "5.00"
