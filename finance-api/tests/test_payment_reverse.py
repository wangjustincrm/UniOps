"""Reverse an executed payment batch whose money never went out, and the Data
Maintenance guard that stops such a batch from being deleted instead.

2026-11-10, production: BP-20261110-0003 was executed by mistake and deleted
in Data Maintenance. PA-20261019-0006 kept a completed payment_record (so it
refused to be paid again), a paid invoice and AP row, a payment posting event
(which would have swallowed the re-payment's event) and a draft JV.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import func, select

from app.core.config import settings
from app.crud import ap_invoice as ap_crud
from app.db.base import get_db
from app.main import app
from app.models.journal_voucher import JournalVoucher
from app.models.mirrors import ExpenseClaim, Invoice, Task
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord
from app.models.payment_batch import PaymentBatch, PaymentBatchLine
from app.models.posting import PostingEvent

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


async def _pa(db, totals=("3367.40",), pa_type="regular", with_task=True):
    """A PO PA over one matched invoice per total, each with a posted AP row."""
    vendor = uuid.uuid4()
    invoices = [Invoice(
        internal_ref=f"INV-{uuid.uuid4().hex[:8]}", vendor_invoice_number=f"V{i + 1}",
        vendor_id=vendor, vendor_name="Micronostyx", amount=Decimal(t), tax_amount=Decimal("0"),
        total_amount=Decimal(t), currency="CAD", invoice_date=date(2026, 10, 1),
        due_date=date(2026, 11, 1), status="matched") for i, t in enumerate(totals)]
    db.add_all(invoices)
    await db.flush()
    for inv in invoices:
        await ap_crud.upsert(db, source="epms", source_invoice_id=inv.id, payload=dict(
            source_ref=inv.internal_ref, vendor_id=vendor, vendor_name="Micronostyx",
            vendor_invoice_number=inv.vendor_invoice_number, amount=str(inv.total_amount),
            tax_amount="0.00", total_amount=str(inv.total_amount), currency="CAD",
            invoice_date=inv.invoice_date, due_date=inv.due_date, status="posted",
            source_status="matched", po_id=None, po_number=None), tax_lines=[])
    pa = PaymentApplication(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="t", pa_type=pa_type, status="approved",
        po_id=uuid.uuid4(), po_number="PO-1", vendor_id=vendor, vendor_name="Micronostyx",
        invoice_ids=[str(i.id) for i in invoices],
        payment_amount=sum(Decimal(t) for t in totals), currency="CAD", created_by=uuid.uuid4())
    db.add(pa)
    await db.flush()
    if with_task:
        db.add(Task(document_type="pa", document_id=pa.id, is_completed=False))
        await db.flush()
    return pa, invoices


async def _execute(client, docs):
    r = await client.post(f"{URL}/batches", headers=_h(), json={"docs": docs})
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    r = await client.post(f"{URL}/batches/{bid}/execute", headers=_h())
    assert r.status_code == 200, r.text
    return bid, r.json()


def _doc(pa, invoices=None):
    d = {"doc_kind": "pa", "doc_id": str(pa.id)}
    if invoices is not None:
        d["invoice_ids"] = [str(i.id) for i in invoices]
    return d


async def _reverse(client, bid, reason="Clicked by mistake; nothing was sent", role="payment_officer"):
    return await client.post(f"{URL}/batches/{bid}/reverse", headers=_h(role), json={"reason": reason})


async def _payment_events(db, pa):
    return (await db.execute(select(func.count()).select_from(PostingEvent).where(
        PostingEvent.source_doc_number == pa.pa_number,
        PostingEvent.event_type == "payment"))).scalar_one()


async def _jvs(db, pa):
    return (await db.execute(
        select(func.count()).select_from(JournalVoucher)
        .join(PostingEvent, PostingEvent.id == JournalVoucher.posting_event_id)
        .where(PostingEvent.source_doc_number == pa.pa_number,
               PostingEvent.event_type == "payment"))).scalar_one()


async def _ap(db, inv):
    return await ap_crud.get_by_source(db, source="epms", source_invoice_id=inv.id)


async def test_reverse_undoes_the_payment_and_the_pa_pays_again(client, db_session):
    pa, (inv,) = await _pa(db_session)
    bid, run = await _execute(client, [_doc(pa)])
    assert run["paid"] == 1
    await db_session.refresh(pa); await db_session.refresh(inv)
    assert pa.status == "processed" and inv.status == "paid"
    assert await _payment_events(db_session, pa) == 1

    r = await _reverse(client, bid)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reversed"

    await db_session.refresh(pa); await db_session.refresh(inv)
    assert pa.status == "approved" and pa.paid_at is None
    assert inv.status == "matched"
    ap = await _ap(db_session, inv)
    await db_session.refresh(ap)
    assert ap.status == "posted" and ap.paid_amount == Decimal("0")
    rec = (await db_session.execute(
        select(PaymentRecord).where(PaymentRecord.pa_id == pa.id))).scalar_one()
    assert rec.status == "cancelled" and "Clicked by mistake" in rec.notes
    assert await _payment_events(db_session, pa) == 0
    assert await _jvs(db_session, pa) == 0
    line = (await db_session.execute(
        select(PaymentBatchLine).where(PaymentBatchLine.batch_id == uuid.UUID(bid)))).scalar_one()
    assert line.status == "reversed"
    task = (await db_session.execute(select(Task).where(Task.document_id == pa.id))).scalar_one()
    assert task.is_completed is False and task.completed_at is None

    # The point of it all: the PA is payable again, and its GL entry is not swallowed.
    _, again = await _execute(client, [_doc(pa)])
    assert again["paid"] == 1 and again["failed"] == 0, again
    await db_session.refresh(pa)
    assert pa.status == "processed"
    assert await _payment_events(db_session, pa) == 1
    assert await _jvs(db_session, pa) == 1


async def test_reverse_needs_a_reason(client, db_session):
    pa, _ = await _pa(db_session)
    bid, _ = await _execute(client, [_doc(pa)])
    r = await _reverse(client, bid, reason="   ")
    assert r.status_code == 409 and "reason" in r.text
    await db_session.refresh(pa)
    assert pa.status == "processed"


async def test_reverse_needs_payment_authority(client, db_session):
    pa, _ = await _pa(db_session)
    bid, _ = await _execute(client, [_doc(pa)])
    r = await _reverse(client, bid, role="ap_clerk")
    assert r.status_code == 403, r.text
    # the admission side: a payment role can
    assert (await _reverse(client, bid, role="finance_manager")).status_code == 200


async def test_only_an_executed_batch_reverses(client, db_session):
    pa, _ = await _pa(db_session)
    r = await client.post(f"{URL}/batches", headers=_h(), json={"docs": [_doc(pa)]})
    r = await _reverse(client, r.json()["id"])
    assert r.status_code == 409 and "Only an executed batch" in r.text


async def test_a_batch_reverses_once(client, db_session):
    pa, _ = await _pa(db_session)
    bid, _ = await _execute(client, [_doc(pa)])
    assert (await _reverse(client, bid)).status_code == 200
    assert (await _reverse(client, bid)).status_code == 409


async def test_a_posted_journal_voucher_refuses_and_changes_nothing(client, db_session):
    pa, (inv,) = await _pa(db_session)
    bid, _ = await _execute(client, [_doc(pa)])
    jv = (await db_session.execute(
        select(JournalVoucher).join(PostingEvent, PostingEvent.id == JournalVoucher.posting_event_id)
        .where(PostingEvent.source_doc_number == pa.pa_number))).scalar_one()
    jv.status = "posted"
    await db_session.flush()

    r = await _reverse(client, bid)
    assert r.status_code == 409 and "already posted" in r.text
    await db_session.refresh(pa); await db_session.refresh(inv)
    assert pa.status == "processed" and inv.status == "paid"
    assert (await db_session.get(PaymentBatch, uuid.UUID(bid))).status == "executed"


async def test_a_claim_line_refuses_the_whole_batch(client, db_session):
    pa, _ = await _pa(db_session)
    claim = ExpenseClaim(
        claim_number=f"EXP-{uuid.uuid4().hex[:8]}", claim_type="EXP", status="approved",
        employee_id=uuid.uuid4(), employee_name="Jane Doe", currency="CAD",
        total_amount=Decimal("75.00"), tax_amount=Decimal("0"), net_amount=Decimal("75.00"))
    db_session.add(claim)
    await db_session.flush()
    bid, run = await _execute(client, [_doc(pa), {"doc_kind": "expense_claim", "doc_id": str(claim.id)}])
    assert run["paid"] == 2

    r = await _reverse(client, bid)
    assert r.status_code == 409 and "expense-claim" in r.text
    await db_session.refresh(pa)
    assert pa.status == "processed"   # all or nothing: the PA line was not reversed either


async def test_reversing_one_invoice_of_a_split_payment(client, db_session):
    pa, invs = await _pa(db_session, totals=("100.00", "30.00", "20.00"))
    bid, run = await _execute(client, [_doc(pa, invs[:1])])
    assert run["paid"] == 1
    await db_session.refresh(invs[0]); await db_session.refresh(pa)
    assert invs[0].status == "paid" and pa.status == "approved"

    assert (await _reverse(client, bid)).status_code == 200
    await db_session.refresh(invs[0]); await db_session.refresh(pa)
    assert invs[0].status == "matched" and pa.status == "approved"

    # the same invoice can be paid again on its own
    _, again = await _execute(client, [_doc(pa, invs[:1])])
    assert again["paid"] == 1, again


async def test_an_invoice_still_covered_by_another_payment_stays_paid(client, db_session):
    """Prepayment then balance on one invoice; reversing the balance leaves it
    partially paid (the prepayment still stands), not unpaid."""
    pre, (inv,) = await _pa(db_session, totals=("1000.00",), pa_type="prepayment", with_task=False)
    pre.payment_amount = Decimal("500.00")
    await db_session.flush()
    await _execute(client, [_doc(pre)])
    bal = PaymentApplication(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="t", pa_type="balance", status="approved",
        po_id=pre.po_id, po_number="PO-1", vendor_id=pre.vendor_id, vendor_name="Micronostyx",
        invoice_ids=[str(inv.id)], payment_amount=Decimal("500.00"), currency="CAD",
        created_by=uuid.uuid4())
    db_session.add(bal)
    await db_session.flush()
    bid, _ = await _execute(client, [_doc(bal)])
    await db_session.refresh(inv)
    assert inv.status == "paid"

    assert (await _reverse(client, bid)).status_code == 200
    await db_session.refresh(inv)
    assert inv.status == "partially_paid"


# ── Data Maintenance ─────────────────────────────────────────────────────────────

ADMIN = "/finance/v1/admin/payment_batch"


async def test_dm_refuses_to_delete_an_executed_batch_until_it_is_reversed(client, db_session):
    pa, _ = await _pa(db_session)
    bid, _ = await _execute(client, [_doc(pa)])

    r = await client.delete(f"{ADMIN}/{bid}?preview=1", headers=_h("system_admin"))
    assert r.status_code == 409 and "Reverse the batch" in r.text
    r = await client.delete(f"{ADMIN}/{bid}", headers=_h("system_admin"))
    assert r.status_code == 409
    r = await client.post(f"{ADMIN}/bulk-delete", headers=_h("system_admin"), json={"ids": [bid]})
    assert r.status_code == 409
    assert await db_session.get(PaymentBatch, uuid.UUID(bid)) is not None

    assert (await _reverse(client, bid)).status_code == 200
    r = await client.delete(f"{ADMIN}/{bid}", headers=_h("system_admin"))
    assert r.status_code == 200, r.text


async def test_dm_still_deletes_a_draft_batch(client, db_session):
    pa, _ = await _pa(db_session)
    r = await client.post(f"{URL}/batches", headers=_h(), json={"docs": [_doc(pa)]})
    r = await client.delete(f"{ADMIN}/{r.json()['id']}", headers=_h("system_admin"))
    assert r.status_code == 200, r.text
