"""Orphaned create_pa task cleanup.

A create_pa task is anchored on a PO and, before this fix, was only ever
completed when a PA was created for that PO. If the matched invoice that raised
the task was later deleted or reverted out of 'matched', the task lingered
forever in the requester's inbox (real prod bug: 3 POs with no invoice still
prompting "Create Payment Application").

The fix has two layers:
  1. Inbox self-heal: get_for_role completes open create_pa tasks whose PO has
     no matched invoice and no PA.
  2. Prevention at the source: unmatch-on-edit (rematch_from_existing) and
     invoice delete complete the PO's create_pa task when no matched invoice
     remains.
A PO that still has a matched invoice (payment genuinely pending) must KEEP its
task.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import invoice as invoice_crud
from app.crud import task as task_crud
from app.crud import user as user_crud
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _requester_vendor_po(db, *, po_status="issued"):
    requester = await user_crud.create(db, RegisterRequest(
        email=f"orphan-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
        full_name="Orphan Requester", role="requester"))
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Orphan Vendor",
                    category="Services", contact_name="C", contact_email="c@o.test")
    db.add(vendor)
    await db.flush()
    pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:6]}", title="Orphan PR", type=2,
                         status="approved", amount=Decimal("100.00"), created_by=requester.id)
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="Orphan PO", type=2,
                       vendor_id=vendor.id, vendor_name=vendor.name, created_by=requester.id,
                       pr_id=pr.id, status=po_status, total=Decimal("113.00"), approval_step_idx=2)
    db.add(po)
    await db.flush()
    pr.po_id = po.id  # reflect the real PR->PO link so create_po doesn't also fire
    await db.flush()
    return requester, vendor, po


def _open_create_pa_task(po, requester_id):
    return Task(
        type="create_pa", priority="normal", document_type="po",
        document_id=po.id, document_number=po.number, assigned_role="requester",
        assigned_user_id=requester_id,
        title=f"Create Payment Application for {po.number}",
        description="Invoices matched to PO await a Payment Application.",
        amount=po.total, vendor=po.vendor_name,
    )


@pytest.mark.asyncio
async def test_get_for_role_completes_orphan_create_pa_task_without_matched_invoice(test_engine):
    """Self-heal: an open create_pa task whose PO has no matched invoice and no PA
    is auto-completed on the next inbox load and no longer surfaces."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester, _vendor, po = await _requester_vendor_po(db)
        task = _open_create_pa_task(po, requester.id)
        db.add(task)
        await db.commit()
        task_id = task.id

        tasks = await task_crud.get_for_role(db, "requester", requester.id)
        await db.commit()

        surfaced = [t for t in tasks if t.type == "create_pa" and t.document_id == po.id]
        assert surfaced == [], "orphan create_pa (no matched invoice, no PA) must not surface"
        healed = await db.get(Task, task_id)
        assert healed.is_completed is True
        assert healed.completed_at is not None


@pytest.mark.asyncio
async def test_get_for_role_keeps_create_pa_task_with_matched_invoice(test_engine):
    """Guard: a PO that still has a matched invoice awaiting a PA keeps its task
    (the legitimate case — must NOT be swept by the self-heal)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester, vendor, po = await _requester_vendor_po(db)
        inv = Invoice(internal_ref=f"IVN-{uuid.uuid4().hex[:6]}", vendor_invoice_number="V-INV-1",
                      vendor_id=vendor.id, vendor_name=vendor.name, amount=Decimal("100.00"),
                      total_amount=Decimal("113.00"), invoice_date=date(2026, 7, 1),
                      due_date=date(2026, 7, 31), status="matched", po_id=po.id,
                      uploaded_by=requester.id, created_at=datetime(2026, 7, 7, tzinfo=timezone.utc))
        db.add(inv)
        db.add(_open_create_pa_task(po, requester.id))
        await db.commit()

        tasks = await task_crud.get_for_role(db, "requester", requester.id)
        await db.commit()

        surfaced = [t for t in tasks if t.type == "create_pa" and t.document_id == po.id]
        assert len(surfaced) == 1, "PO with a matched invoice must keep its create_pa task"
        assert surfaced[0].is_completed is False


@pytest.mark.asyncio
async def test_rematch_unmatch_completes_create_pa_task(test_engine):
    """打回: editing a matched invoice so its allocations no longer balance resets
    it to unmatched — the PO's create_pa task is completed at that moment."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester, vendor, po = await _requester_vendor_po(db)
        line = PoLineItem(po_id=po.id, description="A", qty=Decimal("10"), unit="ea",
                          unit_price=Decimal("10"), line_total=Decimal("100"))
        db.add(line)
        await db.flush()
        inv = Invoice(internal_ref=f"IVN-{uuid.uuid4().hex[:6]}", vendor_invoice_number="V-INV-2",
                      vendor_id=vendor.id, vendor_name=vendor.name, amount=Decimal("100.00"),
                      total_amount=Decimal("100.00"), invoice_date=date(2026, 7, 1),
                      due_date=date(2026, 7, 31), status="matched", po_id=po.id,
                      uploaded_by=requester.id)
        db.add(inv)
        await db.flush()
        # allocation sums to 50, not the invoice's 100 → rematch resets to unmatched
        db.add(InvoicePoAllocation(invoice_id=inv.id, invoice_line_id=uuid.uuid4(), po_id=po.id,
                                   po_line_id=line.id, allocated_amount=Decimal("50"),
                                   allocated_total=Decimal("50")))
        task = _open_create_pa_task(po, requester.id)
        db.add(task)
        await db.commit()
        task_id = task.id

        result = await invoice_crud.rematch_from_existing(db, inv, matched_by=requester.id)
        await db.commit()

        assert result.status == "unmatched"
        healed = await db.get(Task, task_id)
        assert healed.is_completed is True, "unmatch must complete the PO's create_pa task"


@pytest.mark.asyncio
async def test_delete_invoice_completes_create_pa_task(test_engine):
    """删: deleting the (now non-matched) invoice for a PO with no other matched
    invoice completes the lingering create_pa task."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester, vendor, po = await _requester_vendor_po(db)
        inv = Invoice(internal_ref=f"IVN-{uuid.uuid4().hex[:6]}", vendor_invoice_number="V-INV-3",
                      vendor_id=vendor.id, vendor_name=vendor.name, amount=Decimal("100.00"),
                      total_amount=Decimal("100.00"), invoice_date=date(2026, 7, 1),
                      due_date=date(2026, 7, 31), status="unmatched", po_id=po.id,
                      uploaded_by=requester.id)
        db.add(inv)
        task = _open_create_pa_task(po, requester.id)
        db.add(task)
        await db.commit()
        task_id = task.id

        await invoice_crud.delete(db, inv)
        await db.commit()

        healed = await db.get(Task, task_id)
        assert healed.is_completed is True, "deleting the invoice must complete the orphan create_pa task"
