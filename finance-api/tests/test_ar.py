"""Accounts Receivable framework (Phase c): revenue recognition, receipts,
open items / aging, and output tax flowing into the GST/HST return."""
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.ar import ArInvoice
from app.models.posting import PostingEvent, PostingLine


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


def _invoice_body(amount="1000.00", tax=None, **kw):
    body = {
        "customer_id": str(uuid.uuid4()), "customer_name": "Loblaw Inc",
        "invoice_date": "2026-06-03", "due_date": "2026-07-03",
        "currency": "CAD", "amount": amount, "tax_lines": tax or [],
    }
    body.update(kw)
    return body


async def test_create_invoice_computes_totals(client):
    r = await client.post("/finance/v1/ar/invoices", headers=_h(),
                          json=_invoice_body("1000.00", [{"tax_code": "HST_ON", "tax_amount": "130.00"}]))
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["amount"] == "1000.00" and b["tax_amount"] == "130.00" and b["total_amount"] == "1130.00"
    assert b["status"] == "draft" and b["invoice_number"].startswith("AR-")


async def test_post_invoice_revenue_event(client, db_session):
    r = await client.post("/finance/v1/ar/invoices", headers=_h(),
                          json=_invoice_body("1000.00",
                              [{"tax_code": "HST_ON", "tax_amount": "130.00"},
                               {"tax_code": "GST", "tax_amount": "50.00"}]))
    inv_id = r.json()["id"]

    r = await client.post(f"/finance/v1/ar/invoices/{inv_id}/post", headers=_h())
    assert r.status_code == 200 and r.json()["status"] == "posted"

    ev = (await db_session.execute(
        select(PostingEvent).where(PostingEvent.source_doc_id == uuid.UUID(inv_id)))).scalar_one()
    lines = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev.id))).scalars().all()
    by_role = {}
    for l in lines:
        by_role.setdefault(l.line_role, Decimal("0"))
        by_role[l.line_role] += l.debit - l.credit
    # debit AR total, credit revenue pre-tax, credit output tax
    assert by_role["accounts_receivable"] == Decimal("1180.00")   # debit
    assert by_role["revenue"] == Decimal("-1000.00")              # credit
    assert by_role["output_tax"] == Decimal("-180.00")           # credit
    # balanced + account codes stamped
    assert sum((l.debit for l in lines), Decimal("0")) == sum((l.credit for l in lines), Decimal("0"))
    assert {l.account_code for l in lines} == {"1100", "4000", "2200"}
    # output tax coded per tax_code
    out = {l.tax_code: l.credit for l in lines if l.line_role == "output_tax"}
    assert out == {"HST_ON": Decimal("130.00"), "GST": Decimal("50.00")}


async def test_post_is_idempotent(client):
    r = await client.post("/finance/v1/ar/invoices", headers=_h(), json=_invoice_body("500.00"))
    inv_id = r.json()["id"]
    r1 = await client.post(f"/finance/v1/ar/invoices/{inv_id}/post", headers=_h())
    r2 = await client.post(f"/finance/v1/ar/invoices/{inv_id}/post", headers=_h())
    assert r1.json()["posting_event_id"] is not None
    assert r2.json()["already_posted"] is True


async def test_receipt_full_marks_paid(client, db_session):
    r = await client.post("/finance/v1/ar/invoices", headers=_h(),
                          json=_invoice_body("1000.00", [{"tax_code": "HST_ON", "tax_amount": "130.00"}]))
    inv_id = r.json()["id"]
    await client.post(f"/finance/v1/ar/invoices/{inv_id}/post", headers=_h())

    r = await client.post("/finance/v1/ar/receipts", headers=_h(), json={
        "customer_id": str(uuid.uuid4()), "customer_name": "Loblaw Inc",
        "amount": "1130.00", "currency": "CAD", "receipt_date": "2026-06-20",
        "invoice_id": inv_id})
    assert r.status_code == 201 and r.json()["invoice_status"] == "paid"
    assert r.json()["receipt_number"].startswith("RCP-")

    # debit bank / credit AR
    rcp_ev = (await db_session.execute(
        select(PostingEvent).where(PostingEvent.source_doc_type == "ar_receipt"))).scalar_one()
    lines = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == rcp_ev.id))).scalars().all()
    roles = {l.line_role: (l.debit, l.credit, l.account_code) for l in lines}
    assert roles["bank"] == (Decimal("1130.00"), Decimal("0"), "1010")
    assert roles["accounts_receivable"] == (Decimal("0"), Decimal("1130.00"), "1100")


async def test_receipt_partial_then_full(client):
    r = await client.post("/finance/v1/ar/invoices", headers=_h(), json=_invoice_body("1000.00"))
    inv_id = r.json()["id"]
    await client.post(f"/finance/v1/ar/invoices/{inv_id}/post", headers=_h())

    cust = str(uuid.uuid4())
    r = await client.post("/finance/v1/ar/receipts", headers=_h(), json={
        "customer_id": cust, "customer_name": "X", "amount": "400.00",
        "currency": "CAD", "receipt_date": "2026-06-20", "invoice_id": inv_id})
    assert r.json()["invoice_status"] == "partially_paid"

    # still an open item with outstanding 600
    r = await client.get("/finance/v1/ar/open-items", headers=_h())
    item = next(i for i in r.json() if i["invoice_id"] == inv_id)
    assert item["outstanding"] == "600.00" and item["status"] == "partially_paid"

    r = await client.post("/finance/v1/ar/receipts", headers=_h(), json={
        "customer_id": cust, "customer_name": "X", "amount": "600.00",
        "currency": "CAD", "receipt_date": "2026-06-25", "invoice_id": inv_id})
    assert r.json()["invoice_status"] == "paid"


async def test_aging_buckets(client):
    # overdue invoice (due in the past)
    await client.post("/finance/v1/ar/invoices", headers=_h(),
                      json=_invoice_body("800.00", invoice_date="2026-01-01", due_date="2026-01-31"))
    invs = (await client.get("/finance/v1/ar/invoices?status=draft", headers=_h())).json()
    await client.post(f"/finance/v1/ar/invoices/{invs[0]['id']}/post", headers=_h())

    r = await client.get("/finance/v1/ar/aging", headers=_h())
    assert r.status_code == 200
    assert any(Decimal(row["total"]) > 0 for row in r.json())


async def test_void_only_draft(client):
    r = await client.post("/finance/v1/ar/invoices", headers=_h(), json=_invoice_body("100.00"))
    inv_id = r.json()["id"]
    rv = await client.post(f"/finance/v1/ar/invoices/{inv_id}/void", headers=_h())
    assert rv.status_code == 200 and rv.json()["status"] == "void"

    # posting a void invoice is rejected
    r = await client.post("/finance/v1/ar/invoices", headers=_h(), json=_invoice_body("100.00"))
    inv2 = r.json()["id"]
    await client.post(f"/finance/v1/ar/invoices/{inv2}/post", headers=_h())
    rv = await client.post(f"/finance/v1/ar/invoices/{inv2}/void", headers=_h())
    assert rv.status_code == 409   # posted → reversal deferred to GL


async def test_output_tax_flows_into_gst_return(client):
    """AR output tax + AP-style ITC net out in the GST/HST return.

    The tax return groups by the posting event's fiscal_period, which
    emit_event stamps with TODAY's month — query dynamically (a hardcoded
    month rots at rollover, same class as the old test_gl failures)."""
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    r = await client.post("/finance/v1/ar/invoices", headers=_h(),
                          json=_invoice_body("1000.00",
                              [{"tax_code": "HST_ON", "tax_amount": "130.00"}],
                              invoice_date="2026-06-10", due_date="2026-07-10"))
    await client.post(f"/finance/v1/ar/invoices/{r.json()['id']}/post", headers=_h())

    rep = (await client.get(f"/finance/v1/tax/gst-hst-return?period={period}", headers=_h())).json()
    out = {row["tax_code"]: row["output_tax"] for row in rep["output_tax_by_code"]}
    assert out.get("HST_ON") == "130.00"
    assert rep["output_tax_total"] == "130.00"
    # net = output - itc; with no ITC this period it's +130 owing
    assert rep["net_tax"] == "130.00"


async def test_ar_invoice_number_survives_gap(client, db_session):
    """Regression: AR invoice number keys off the max tail, not count(*).

    A renumbered/deleted invoice leaves count() lagging the real max, so count()+1
    reissues an already-existing number -> UniqueViolation on uq_ar_invoices_number.
    Reproduce the gap, expect a fresh number instead of a collision."""
    from sqlalchemy import text

    r1 = await client.post("/finance/v1/ar/invoices", headers=_h(), json=_invoice_body())
    r2 = await client.post("/finance/v1/ar/invoices", headers=_h(), json=_invoice_body())
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)
    n2 = r2.json()["invoice_number"]

    # Move r1 out of today's AR-<today> window: count() now lags the real max tail.
    await db_session.execute(
        text("UPDATE ar_invoices SET invoice_number = :n WHERE id = :i"),
        {"n": "AR-19000101-0001", "i": r1.json()["id"]},
    )
    await db_session.flush()

    # Under the old count()+1 this reissues r2's number -> 500 (UniqueViolation).
    r3 = await client.post("/finance/v1/ar/invoices", headers=_h(), json=_invoice_body())
    assert r3.status_code == 201, r3.text
    assert r3.json()["invoice_number"] != n2, "reissued an existing AR invoice number"
