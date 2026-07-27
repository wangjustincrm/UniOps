"""Stale place_order task cleanup (Task Inbox reconciliation).

place_order is valid only while a PO is 'approved' and not yet placed. The live
place_order() action flips the PO to 'issued' AND completes the task
(_complete_tasks). But PMS-imported / bulk-synced POs were set straight to
issued / received WITHOUT going through that action, so their place_order tasks
were never completed and linger forever in Procurement's inbox (real prod:
11 place_order tasks on issued/fully_received POs).

get_for_role must self-heal: complete open place_order tasks whose PO is no
longer 'approved'. An approved-but-unplaced PO must KEEP its task.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import task as task_crud
from app.crud import user as user_crud
from app.models.po import PurchaseOrder
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _officer_vendor_po(db, *, po_status):
    officer = await user_crud.create(db, RegisterRequest(
        email=f"po-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="Proc Officer", role="procurement_officer"))
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="PlaceOrder Vendor",
                    category="Services", contact_name="C", contact_email="c@p.test")
    db.add(vendor)
    await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="PlaceOrder PO", type=2,
                       vendor_id=vendor.id, vendor_name=vendor.name, created_by=officer.id,
                       status=po_status, total=Decimal("100.00"), approval_step_idx=2)
    db.add(po)
    await db.flush()
    return officer, po


def _open_place_order_task(po):
    return Task(
        type="place_order", priority="normal", document_type="po",
        document_id=po.id, document_number=po.number, assigned_role="procurement_officer",
        title=f"Place Order: {po.number}",
        description="PO approved. Please place the order with the vendor.",
        amount=po.total, vendor=po.vendor_name,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("po_status", ["issued", "partially_received", "fully_received", "closed", "cancelled"])
async def test_place_order_task_completed_when_po_no_longer_approved(test_engine, po_status):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        officer, po = await _officer_vendor_po(db, po_status=po_status)
        task = _open_place_order_task(po)
        db.add(task)
        await db.commit()
        task_id = task.id

        tasks = await task_crud.get_for_role(db, "procurement_officer", officer.id)
        await db.commit()

        surfaced = [t for t in tasks if t.type == "place_order" and t.document_id == po.id]
        assert surfaced == [], f"place_order on '{po_status}' PO must not surface"
        healed = await db.get(Task, task_id)
        assert healed.is_completed is True
        assert healed.completed_at is not None


@pytest.mark.asyncio
async def test_place_order_task_kept_when_po_still_approved(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        officer, po = await _officer_vendor_po(db, po_status="approved")
        # place_order_method NULL → genuinely awaiting placement
        db.add(_open_place_order_task(po))
        await db.commit()

        tasks = await task_crud.get_for_role(db, "procurement_officer", officer.id)
        await db.commit()

        surfaced = [t for t in tasks if t.type == "place_order" and t.document_id == po.id]
        assert len(surfaced) == 1, "approved-but-unplaced PO must keep its place_order task"
        assert surfaced[0].is_completed is False
