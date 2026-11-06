"""Paying some of a PA's invoices in a run (Finance, 2026-11-06)."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.crud import remittance as rem
from app.db.base import get_db
from app.main import app
from app.models.mirrors import Invoice, Task
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord
from app.models.posting import PostingEvent, PostingLine
from app.models.vendor_credit import VendorCredit

URL = "/finance/v1/payments"


def _h(role="payment_officer"):
    token = jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                        "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                       settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _pa(db, totals=("100.00", "30.00", "20.00"), amount=None, pa_type="regular"):
    """A PA over one invoice per total, for their sum unless `amount` is given."""
    vendor = uuid.uuid4()
    invoices = [Invoice(
        internal_ref=f"INV-{uuid.uuid4().hex[:8]}", vendor_invoice_number=f"V{i + 1}",
        vendor_id=vendor, vendor_name="ACME", amount=Decimal(t), tax_amount=Decimal("0"),
        total_amount=Decimal(t), currency="CAD", invoice_date=date(2026, 6, 1),
        due_date=date(2026, 7, 1 + i), status="matched") for i, t in enumerate(totals)]
    db.add_all(invoices)
    await db.flush()
    pa = PaymentApplication(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="t", pa_type=pa_type, status="approved",
        po_id=uuid.uuid4(), po_number="PO-1", vendor_id=vendor, vendor_name="ACME",
        invoice_ids=[str(i.id) for i in invoices],
        payment_amount=Decimal(amount) if amount else sum(Decimal(t) for t in totals),
        currency="CAD", created_by=uuid.uuid4())
    db.add(pa)
    await db.flush()
    return pa, invoices


async def _draft(client, pa, invoices=None):
    doc = {"doc_kind": "pa", "doc_id": str(pa.id)}
    if invoices is not None:
        doc["invoice_ids"] = [str(i.id) for i in invoices]
    return await client.post(f"{URL}/batches", headers=_h(), json={"docs": [doc]})


async def _run(client, pa, invoices=None):
    r = await _draft(client, pa, invoices)
    assert r.status_code == 201, r.text
    r = await client.post(f"{URL}/batches/{r.json()['id']}/execute", headers=_h())
    assert r.status_code == 200, r.text
    return r.json()


async def _due_row(client, pa):
    rows = (await client.get(f"{URL}/due", headers=_h())).json()
    return next((d for d in rows if d["doc_id"] == str(pa.id)), None)


async def test_due_row_offers_invoices_only_when_they_add_up(client, db_session):
    fits, _ = await _pa(db_session)
    short, _ = await _pa(db_session, amount="140.00")     # 150 of invoices on a 140 PA
    single, _ = await _pa(db_session, totals=("100.00",))
    prepay, _ = await _pa(db_session, pa_type="prepayment")
    row = await _due_row(client, fits)
    assert row["splittable"] is True
    assert [(i["number"], i["amount"], i["paid"]) for i in row["invoices"]] == [
        ("V1", "100.00", False), ("V2", "30.00", False), ("V3", "20.00", False)]
    for pa in (short, single, prepay):
        assert (await _due_row(client, pa))["splittable"] is False


async def test_a_batch_line_carries_only_the_picked_invoices(client, db_session):
    pa, inv = await _pa(db_session)
    r = await _draft(client, pa, inv[1:])
    assert r.status_code == 201, r.text
    assert r.json()["total"] == "50.00"
    detail = (await client.get(f"{URL}/batches/{r.json()['id']}", headers=_h())).json()
    line = detail["lines"][0]
    assert line["amount"] == "50.00" and line["vendor_inv_no"] == "V2, V3"
    assert line["invoice_ids"] == [str(i.id) for i in inv[1:]]


async def test_picking_every_invoice_is_an_ordinary_whole_payment(client, db_session):
    pa, inv = await _pa(db_session)
    assert (await _run(client, pa, inv))["paid"] == 1
    rec = (await db_session.execute(
        select(PaymentRecord).where(PaymentRecord.pa_id == pa.id))).scalar_one()
    assert rec.invoice_ids is None and rec.amount == Decimal("150.00")
    assert pa.status == "processed"


async def test_a_pa_whose_invoices_do_not_add_up_cannot_be_split(client, db_session):
    pa, inv = await _pa(db_session, amount="140.00")
    r = await _draft(client, pa, inv[:1])
    assert r.status_code == 409 and "as a whole" in r.json()["detail"]
    assert (await _draft(client, pa)).status_code == 201     # whole is still fine


async def test_partial_payment_settles_only_those_invoices(client, db_session):
    pa, inv = await _pa(db_session)
    db_session.add(Task(document_type="pa", document_id=pa.id, is_completed=False))
    await db_session.flush()

    assert (await _run(client, pa, inv[:1]))["paid"] == 1

    assert pa.status == "approved" and pa.paid_at is None
    assert [i.status for i in inv] == ["paid", "matched", "matched"]
    task = (await db_session.execute(select(Task).where(Task.document_id == pa.id))).scalar_one()
    assert task.is_completed is False
    row = await _due_row(client, pa)
    assert row["amount"] == "50.00" and row["payment_amount"] == "150.00"
    assert [i["paid"] for i in row["invoices"]] == [True, False, False]
    assert row["due_date"] == "2026-07-02"       # the paid invoice's date no longer counts


async def test_every_split_payment_reaches_the_ledger(client, db_session):
    """posting_events is unique per document and type: keyed on the PA, the
    second run would pay the bank and post nothing."""
    pa, inv = await _pa(db_session)
    await _run(client, pa, inv[:1])
    await _run(client, pa, inv[1:2])
    await _run(client, pa, inv[2:])

    assert pa.status == "processed" and pa.paid_at is not None
    assert [i.status for i in inv] == ["paid", "paid", "paid"]
    assert await _due_row(client, pa) is None
    records = (await db_session.execute(
        select(PaymentRecord).where(PaymentRecord.pa_id == pa.id))).scalars().all()
    assert sorted(r.amount for r in records) == [Decimal("20"), Decimal("30"), Decimal("100")]
    events = (await db_session.execute(select(PostingEvent).where(
        PostingEvent.source_doc_number == pa.pa_number,
        PostingEvent.event_type == "payment"))).scalars().all()
    assert len(events) == 3
    debits = (await db_session.execute(select(PostingLine.debit).where(
        PostingLine.event_id.in_([e.id for e in events]),
        PostingLine.line_role == "accounts_payable"))).scalars().all()
    assert sum(debits) == Decimal("150.00")


async def test_an_invoice_is_not_paid_twice(client, db_session):
    pa, inv = await _pa(db_session)
    stale = (await _draft(client, pa, inv[:2])).json()     # holds V1 + V2
    await _run(client, pa, inv[:1])                        # V1 goes out in another run

    r = await client.post(f"{URL}/batches/{stale['id']}/execute", headers=_h())
    assert r.json()["paid"] == 0 and "already paid" in r.json()["lines"][0]["error"]
    assert (await _draft(client, pa, inv[:1])).status_code == 409
    assert inv[1].status == "matched"


async def test_a_whole_line_drafted_before_a_partial_payment_is_refused(client, db_session):
    """The operator saw 150 on that draft; 50 is what is left."""
    pa, inv = await _pa(db_session)
    stale = (await _draft(client, pa)).json()
    await _run(client, pa, inv[:1])

    r = await client.post(f"{URL}/batches/{stale['id']}/execute", headers=_h())
    assert r.json()["paid"] == 0 and "Cancel this draft" in r.json()["lines"][0]["error"]
    assert pa.status == "approved"


async def test_paying_a_part_paid_pa_from_elsewhere_pays_the_remainder(client, db_session):
    """EPMS Process sends no invoices. It must not send the full amount again."""
    pa, inv = await _pa(db_session)
    await _run(client, pa, inv[:1])

    r = await client.post(f"{URL}/execute", headers=_h(),
                          json={"doc_kind": "pa", "doc_id": str(pa.id)})
    assert r.status_code == 200, r.text
    assert r.json()["new_status"] == "processed"
    records = (await db_session.execute(
        select(PaymentRecord).where(PaymentRecord.pa_id == pa.id))).scalars().all()
    assert sum(r.amount for r in records) == Decimal("150.00")


async def test_credit_on_a_partial_payment_counts_towards_the_pa(client, db_session):
    pa, inv = await _pa(db_session)
    db_session.add(VendorCredit(
        credit_number=f"VC-{uuid.uuid4().hex[:8]}", vendor_id=pa.vendor_id, vendor_name="ACME",
        vendor_credit_number="CN1", credit_date=date(2026, 7, 1), currency="CAD",
        amount=Decimal("40"), tax_amount=Decimal("0"), total_amount=Decimal("40"),
        applied_amount=Decimal("0"), remaining_amount=Decimal("40"), status="available",
        line_items=[], uploaded_by=uuid.uuid4(), uploaded_at=datetime.now(timezone.utc)))
    await db_session.flush()

    await _run(client, pa, inv[:1])              # 100: 40 of credit + 60 of cash

    rec = (await db_session.execute(
        select(PaymentRecord).where(PaymentRecord.pa_id == pa.id))).scalar_one()
    assert (rec.amount, rec.credit_applied) == (Decimal("60.00"), Decimal("40.00"))
    assert (await _due_row(client, pa))["amount"] == "50.00"


async def test_credit_preview_is_for_what_is_being_paid(client, db_session):
    pa, inv = await _pa(db_session)
    url = f"/finance/v1/vendor-credits/suggest?doc_kind=pa&doc_id={pa.id}"
    assert (await client.get(url + "&amount=30.00", headers=_h())).json()["gross"] == "30.00"
    await _run(client, pa, inv[:1])
    body = (await client.get(url, headers=_h())).json()
    assert body["gross"] == "50.00" and body["already_paid"] == "100.00"


async def test_remittance_names_only_the_invoices_paid(client, db_session):
    pa, inv = await _pa(db_session)
    await _run(client, pa, inv[1:])
    records = (await db_session.execute(
        select(PaymentRecord).where(PaymentRecord.pa_id == pa.id))).scalars().all()
    groups = await rem.build_groups(db_session, records)
    assert [l.vendor_inv_no for l in groups[0].lines] == ["V2, V3"]
