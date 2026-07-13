"""Void-on-source-delete semantics for finance-owned AP invoices.

When a source (EPMS/OA) deletes its invoice it pushes status="void":
  - never-posted AP (no GL postings, nothing paid) → hard-delete row + tax lines
  - posted AP (accrual already in GL) → keep + void + emit accrual_reversal
Manual /void keeps the row but must also reverse an existing accrual.
Deleting rows also means AP numbering must not reuse a taken number.
"""
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
from app.models.ap_invoice import ApInvoice, ApInvoiceTaxLine
from app.models.posting import PostingEvent, PostingLine

URL = "/finance/v1/ap/invoices"


def _token(role="ap_clerk"):
    return jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h(role="ap_clerk"):
    return {"Authorization": f"Bearer {_token(role)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _body(src_id, status="draft", with_tax=True, **over):
    b = dict(
        source="epms", source_invoice_id=str(src_id), source_ref="INV-X",
        vendor_id=str(uuid.uuid4()), vendor_name="ACME Dairy Supplies",
        vendor_invoice_number="V-1", amount="1000.00", tax_amount="130.00",
        total_amount="1130.00", currency="CAD",
        invoice_date="2026-06-01", due_date="2026-07-01",
        status=status, source_status="unmatched",
        tax_lines=([{"line_no": 1, "tax_code": "HST_ON", "taxable_base": "1000.00",
                     "tax_amount": "130.00", "recoverable": True}] if with_tax else []),
    )
    b.update(over)
    return b


async def _row(db, ap_id):
    return (await db.execute(
        select(ApInvoice).where(ApInvoice.id == ap_id))).scalar_one_or_none()


async def _events(db, ap_id, event_type=None):
    q = select(PostingEvent).where(PostingEvent.source_doc_type == "ap_invoice",
                                   PostingEvent.source_doc_id == ap_id)
    if event_type:
        q = q.where(PostingEvent.event_type == event_type)
    return (await db.execute(q)).scalars().all()


async def test_void_sync_deletes_never_posted_ap(client, db_session):
    src = uuid.uuid4()
    r = await client.post(URL, json=_body(src, status="draft"), headers=_h())
    assert r.status_code == 200, r.text
    ap_id = uuid.UUID(r.json()["id"])

    r2 = await client.post(URL, json=_body(src, status="void"), headers=_h())
    assert r2.status_code == 200, r2.text

    assert await _row(db_session, ap_id) is None
    tax = (await db_session.execute(select(ApInvoiceTaxLine).where(
        ApInvoiceTaxLine.invoice_id == ap_id))).scalars().all()
    assert tax == []


async def test_void_sync_keeps_posted_ap_and_reverses_accrual(client, db_session):
    src = uuid.uuid4()
    r = await client.post(URL, json=_body(src, status="posted", source_status="matched"),
                          headers=_h())
    assert r.status_code == 200, r.text
    ap_id = uuid.UUID(r.json()["id"])
    assert len(await _events(db_session, ap_id, "accrual")) == 1

    r2 = await client.post(URL, json=_body(src, status="void"), headers=_h())
    assert r2.status_code == 200, r2.text

    row = await _row(db_session, ap_id)
    assert row is not None and row.status == "void"
    # original accrual untouched, reversal emitted
    assert len(await _events(db_session, ap_id, "accrual")) == 1
    revs = await _events(db_session, ap_id, "accrual_reversal")
    assert len(revs) == 1
    lines = (await db_session.execute(select(PostingLine).where(
        PostingLine.event_id == revs[0].id).order_by(PostingLine.line_no))).scalars().all()
    by_role = {}
    for ln in lines:
        by_role.setdefault(ln.line_role, []).append(ln)
    # mirror of the accrual: AP debited back, expense/ITC credited back
    assert by_role["accounts_payable"][0].debit == Decimal("1130.00")
    assert by_role["accounts_payable"][0].account_code == "2000"
    assert by_role["purchase_expense"][0].credit == Decimal("1000.00")
    assert by_role["sales_tax"][0].credit == Decimal("130.00")
    assert by_role["sales_tax"][0].tax_code == "HST_ON"
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines)


async def test_void_sync_reversal_is_idempotent(client, db_session):
    src = uuid.uuid4()
    r = await client.post(URL, json=_body(src, status="posted", source_status="matched"),
                          headers=_h())
    ap_id = uuid.UUID(r.json()["id"])
    await client.post(URL, json=_body(src, status="void"), headers=_h())
    await client.post(URL, json=_body(src, status="void"), headers=_h())
    assert len(await _events(db_session, ap_id, "accrual_reversal")) == 1


async def test_void_sync_for_unknown_source_creates_nothing(client, db_session):
    src = uuid.uuid4()
    r = await client.post(URL, json=_body(src, status="void"), headers=_h())
    assert r.status_code == 200, r.text
    rows = (await db_session.execute(select(ApInvoice).where(
        ApInvoice.source_invoice_id == src))).scalars().all()
    assert rows == []


async def test_void_sync_keeps_partially_paid_unposted_ap(client, db_session):
    src = uuid.uuid4()
    r = await client.post(URL, json=_body(src, status="draft"), headers=_h())
    ap_id = uuid.UUID(r.json()["id"])
    row = await _row(db_session, ap_id)
    row.paid_amount = Decimal("50.00")   # money moved — never hard-delete
    await db_session.commit()

    r2 = await client.post(URL, json=_body(src, status="void"), headers=_h())
    assert r2.status_code == 200, r2.text
    row = await _row(db_session, ap_id)
    assert row is not None and row.status == "void"


async def test_manual_void_reverses_accrual(client, db_session):
    src = uuid.uuid4()
    r = await client.post(URL, json=_body(src, status="posted", source_status="matched"),
                          headers=_h())
    ap_id = uuid.UUID(r.json()["id"])

    r2 = await client.post(f"{URL}/{ap_id}/void", headers=_h(role="finance_manager"))
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "void"
    assert len(await _events(db_session, ap_id, "accrual_reversal")) == 1


async def test_ap_numbering_survives_deletion(client, db_session):
    """count-based numbering reuses a taken number after a delete — must not."""
    src_a, src_b = uuid.uuid4(), uuid.uuid4()
    ra = await client.post(URL, json=_body(src_a, status="draft"), headers=_h())
    rb = await client.post(URL, json=_body(src_b, status="draft"), headers=_h())
    num_b = rb.json()["ap_invoice_number"]

    # delete A via void sync, then create a third invoice the same day
    await client.post(URL, json=_body(src_a, status="void"), headers=_h())
    rc = await client.post(URL, json=_body(uuid.uuid4(), status="draft"), headers=_h())
    assert rc.status_code == 200, rc.text
    assert rc.json()["ap_invoice_number"] != num_b
