"""AP accrual posting — Phase a A2."""
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
from app.models.ap_invoice import ApInvoice
from app.models.mirrors import Invoice, InvoiceTaxLine
from app.models.posting import PostingEvent, PostingLine


def _token(role="ap_clerk"):
    return jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h():
    return {"Authorization": f"Bearer {_token()}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _invoice(status="matched", amount="100.00", tax="13.00") -> Invoice:
    """Build an EPMS mirror invoice — the bridge `/ap/post-invoice` reads this,
    upserts it into ap_invoices (posted), then accrues."""
    return Invoice(
        internal_ref=f"INV-{uuid.uuid4().hex[:8]}", vendor_invoice_number="VI-9",
        vendor_id=uuid.uuid4(), vendor_name="ACME Dairy Supplies",
        amount=Decimal(amount), tax_amount=Decimal(tax),
        total_amount=Decimal(amount) + Decimal(tax), currency="CAD",
        invoice_date=date(2026, 6, 1), due_date=date(2026, 7, 1), status=status,
    )


async def _ap_id_for(db, mirror_id):
    """The bridge upserts an ap_invoice keyed on (epms, mirror_id); posting rows
    hang off the ap_invoice id, not the mirror id."""
    ap = (await db.execute(select(ApInvoice).where(
        ApInvoice.source == "epms", ApInvoice.source_invoice_id == mirror_id))).scalar_one()
    return ap.id


async def _lines_of(db, ap_id):
    ev = (await db.execute(select(PostingEvent).where(
        PostingEvent.source_doc_id == ap_id,
        PostingEvent.event_type == "accrual"))).scalar_one()
    rows = (await db.execute(select(PostingLine).where(PostingLine.event_id == ev.id)
                             .order_by(PostingLine.line_no))).scalars().all()
    return ev, rows


async def test_accrual_with_tax_lines_splits_itc(client, db_session):
    inv = _invoice(tax="20.00")
    db_session.add(inv)
    await db_session.flush()
    db_session.add_all([
        InvoiceTaxLine(invoice_id=inv.id, line_no=1, tax_code="HST_ON",
                       tax_amount=Decimal("13.00"), recoverable=True),
        InvoiceTaxLine(invoice_id=inv.id, line_no=2, tax_code="PST_BC",
                       tax_amount=Decimal("7.00"), recoverable=False),
    ])
    await db_session.flush()

    r = await client.post("/finance/v1/ap/post-invoice",
                          json={"invoice_id": str(inv.id)}, headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["already_accrued"] is False

    ev, lines = await _lines_of(db_session, await _ap_id_for(db_session, inv.id))
    by_role = {}
    for ln in lines:
        by_role.setdefault(ln.line_role, []).append(ln)

    # non-recoverable PST folded into expense: 100 + 7 = 107
    assert by_role["purchase_expense"][0].debit == Decimal("107.00")
    assert by_role["purchase_expense"][0].account_code == "5000"
    assert by_role["purchase_expense"][0].partner_name == "ACME Dairy Supplies"
    # recoverable HST is the ITC line with its tax code
    assert by_role["sales_tax"][0].debit == Decimal("13.00")
    assert by_role["sales_tax"][0].tax_code == "HST_ON"
    assert by_role["sales_tax"][0].account_code == "1400"
    # AP credit = total
    assert by_role["accounts_payable"][0].credit == Decimal("120.00")
    assert by_role["accounts_payable"][0].account_code == "2000"
    # balanced
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines)


async def test_accrual_without_tax_lines_flags_null_code(client, db_session):
    inv = _invoice()
    db_session.add(inv)
    await db_session.flush()

    r = await client.post("/finance/v1/ap/post-invoice",
                          json={"invoice_id": str(inv.id)}, headers=_h())
    assert r.status_code == 200
    _, lines = await _lines_of(db_session, await _ap_id_for(db_session, inv.id))
    tax = [l for l in lines if l.line_role == "sales_tax"][0]
    assert tax.debit == Decimal("13.00")
    assert tax.tax_code is None   # surfaces on the A5 exception list


async def test_accrual_is_idempotent(client, db_session):
    inv = _invoice()
    db_session.add(inv)
    await db_session.flush()
    r1 = await client.post("/finance/v1/ap/post-invoice",
                           json={"invoice_id": str(inv.id)}, headers=_h())
    r2 = await client.post("/finance/v1/ap/post-invoice",
                           json={"invoice_id": str(inv.id)}, headers=_h())
    assert r1.json()["already_accrued"] is False
    assert r2.json()["already_accrued"] is True
    ap_id = await _ap_id_for(db_session, inv.id)
    evs = (await db_session.execute(select(PostingEvent).where(
        PostingEvent.source_doc_id == ap_id))).scalars().all()
    assert len(evs) == 1


async def test_unknown_invoice_404(client, db_session):
    """The bridge 404s when the EPMS mirror invoice does not exist."""
    r = await client.post("/finance/v1/ap/post-invoice",
                          json={"invoice_id": str(uuid.uuid4())}, headers=_h())
    assert r.status_code == 404
