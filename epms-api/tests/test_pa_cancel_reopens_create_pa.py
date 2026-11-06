"""A PA that is rejected / cancelled no longer covers its PO (ISS-0172, 29/10).

PRD §3.14.2 Completion / INV-013: create_pa tasks "reopen if the PO later stops
being covered by any PA". Rejecting a PA turns it Cancelled and frees its
invoice, but the PO's create_pa task stayed done — nobody was told to raise a
new PA. A returned PA is still live (it comes back to its creator), so it keeps
covering the PO, and so does any other live PA on the PO.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import task as task_crud
from app.crud import user as user_crud
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.pa_po_link import PaPoLink
from app.models.po import PurchaseOrder
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _seed(test_engine, *, other_pa_status=None):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        req = await user_crud.create(db, RegisterRequest(
            email=f"r-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Req Ester", role="requester"))
        v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
                   contact_name="C", contact_email="c@x.com")
        db.add(v)
        await db.flush()
        po = PurchaseOrder(number=f"PO-x{uuid.uuid4().hex[:6]}", title="T", type=2,
                           vendor_id=v.id, vendor_name="Acme", status="fully_received",
                           created_by=req.id, subtotal=Decimal("100"), total=Decimal("100"))
        db.add(po)
        await db.flush()
        inv = Invoice(
            internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
            vendor_id=v.id, vendor_name="Acme", amount=Decimal("100"),
            tax_amount=Decimal("0"), total_amount=Decimal("100"),
            invoice_date=date(2026, 10, 1), due_date=date(2026, 11, 1),
            status="matched", match_route="po", po_id=po.id, uploaded_by=req.id,
            matched_at=datetime.now(timezone.utc),
        )
        db.add(inv)
        await db.flush()
        pas = []
        for status in ["submitted"] + ([other_pa_status] if other_pa_status else []):
            pa = PaymentApplication(
                pa_number=f"PA-x{uuid.uuid4().hex[:6]}", title="t", status=status,
                pa_type="regular", subtotal=Decimal("100"), payment_amount=Decimal("100"),
                vendor_id=v.id, vendor_name="Acme", created_by=req.id,
                po_id=po.id, po_number=po.number, invoice_ids=[str(inv.id)],
            )
            db.add(pa)
            await db.flush()
            db.add(PaPoLink(pa_id=pa.id, po_id=po.id, po_number=po.number))
            pas.append(pa)
        # The task creating the PA completed (crud.pa._complete_create_pa_tasks).
        task = Task(type="create_pa", priority="normal", document_type="po",
                    document_id=po.id, document_number=po.number,
                    assigned_role="requester", assigned_user_id=req.id,
                    title=f"Create Payment Application for {po.number}",
                    is_completed=True, completed_at=datetime.now(timezone.utc))
        db.add(task)
        await db.commit()
        return req, po, pas[0], task.id


def _fake_delegate(test_engine, new_status):
    """approval-api stand-in: flips the PA the way the engine's reject does."""
    async def _fake(doc_type, doc_id, action, comment, token):
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            await db.execute(update(PaymentApplication)
                             .where(PaymentApplication.id == uuid.UUID(doc_id))
                             .values(status=new_status))
            await db.commit()
        return {"new_status": new_status}
    return _fake


async def _task(test_engine, task_id):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        return (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()


@pytest.mark.asyncio
async def test_rejecting_the_only_pa_reopens_create_pa(admin_client, test_engine, monkeypatch):
    req, po, pa, task_id = await _seed(test_engine)
    monkeypatch.setattr("app.api.v1.pa.delegate_action", _fake_delegate(test_engine, "cancelled"))

    r = await admin_client.post(f"/api/v1/pa/{pa.id}/action",
                                json={"action": "reject", "comment": "wrong amount"})
    assert r.status_code == 200, r.text
    assert (await _task(test_engine, task_id)).is_completed is False

    # The inbox self-heal must not close it again: a cancelled PA covers nothing.
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        tasks = await task_crud.get_for_role(db, "requester", req.id)
        await db.commit()
    assert any(t.id == task_id and not t.is_completed for t in tasks)


@pytest.mark.asyncio
async def test_returned_pa_still_covers_the_po(admin_client, test_engine, monkeypatch):
    req, po, pa, task_id = await _seed(test_engine)
    monkeypatch.setattr("app.api.v1.pa.delegate_action", _fake_delegate(test_engine, "returned"))

    r = await admin_client.post(f"/api/v1/pa/{pa.id}/action",
                                json={"action": "return", "comment": "fix it"})
    assert r.status_code == 200, r.text
    assert (await _task(test_engine, task_id)).is_completed is True


@pytest.mark.asyncio
async def test_rejecting_one_pa_keeps_task_done_while_another_live_pa_covers(
    admin_client, test_engine, monkeypatch,
):
    req, po, pa, task_id = await _seed(test_engine, other_pa_status="processed")
    monkeypatch.setattr("app.api.v1.pa.delegate_action", _fake_delegate(test_engine, "cancelled"))

    r = await admin_client.post(f"/api/v1/pa/{pa.id}/action",
                                json={"action": "reject", "comment": "dup"})
    assert r.status_code == 200, r.text
    assert (await _task(test_engine, task_id)).is_completed is True
