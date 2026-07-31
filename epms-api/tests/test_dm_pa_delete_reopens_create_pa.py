"""Data Maintenance PA delete must reopen the PO's create_pa task.

Creating a PA completes the PO's create_pa task (crud.pa._complete_create_pa_tasks).
When the last PA for a PO is deleted via Data Maintenance, that task must be
REOPENED so the requester can create a PA again — otherwise the create_pa prompt
(Task Inbox + the requester's only entry point) is lost while the PO still needs
a PA. If another PA still covers the PO, the task stays completed.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.db.session as session_module
from app.admin.registry import _pa_delete
from app.crud import user as user_crud
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _seed_po_with_pa(db, *, n_pas=1, create_pa_done=True):
    u = await user_crud.create(db, RegisterRequest(
        email=f"dm-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="DM Tester", role="requester"))
    await db.commit()
    v = Vendor(id=uuid.uuid4(), code=f"V-{uuid.uuid4().hex[:8]}", name="Acme",
               category="supplier", contact_name="C", contact_email="c@acme.com")
    db.add(v)
    await db.flush()
    po = PurchaseOrder(id=uuid.uuid4(), number=f"PO-DM-{uuid.uuid4().hex[:6]}", title="t",
                       type=1, status="fully_received", vendor_id=v.id, vendor_name="Acme",
                       created_by=u.id)
    db.add(po)
    await db.flush()
    task = Task(type="create_pa", document_type="po", document_id=po.id,
                document_number=po.number, assigned_role="requester", title="Create PA",
                is_completed=create_pa_done,
                completed_at=datetime.now(timezone.utc) if create_pa_done else None)
    db.add(task)
    pas = []
    for _ in range(n_pas):
        pa = PaymentApplication(
            pa_number=f"PA-DM-{uuid.uuid4().hex[:6]}", title="t", status="approved",
            pa_type="regular", subtotal=Decimal("10"), payment_amount=Decimal("10"),
            vendor_id=v.id, vendor_name="Acme", created_by=u.id, po_id=po.id, po_number=po.number,
        )
        db.add(pa)
        pas.append(pa)
    await db.flush()
    return po, task, pas


async def test_deleting_last_pa_reopens_create_pa_task():
    async with session_module.AsyncSessionLocal() as db:
        po, task, pas = await _seed_po_with_pa(db, n_pas=1)
        assert task.is_completed is True

        await _pa_delete(db, pas[0])
        await db.flush()

        await db.refresh(task)
        assert task.is_completed is False       # reopened
        assert task.completed_at is None
        assert (await db.execute(select(PaymentApplication).where(
            PaymentApplication.po_id == po.id))).scalars().first() is None  # PA gone
        await db.rollback()


async def test_deleting_one_of_two_pas_keeps_task_completed():
    async with session_module.AsyncSessionLocal() as db:
        po, task, pas = await _seed_po_with_pa(db, n_pas=2)

        await _pa_delete(db, pas[0])
        await db.flush()

        await db.refresh(task)
        assert task.is_completed is True        # another PA still covers the PO
        await db.rollback()
