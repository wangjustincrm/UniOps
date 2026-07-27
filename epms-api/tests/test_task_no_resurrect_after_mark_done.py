"""Manual "Mark Done" must be permanent — the read-side backfills must not
resurrect a task the user explicitly completed.

Prod bug (Task Inbox): clicking "Mark Done" on a Place Order task made it come
back as TWO copies. Root cause:
  1. Mark Done (POST /tasks/{id}/complete) only flips is_completed; it does NOT
     advance the PO, which stays 'approved' + place_order_method NULL.
  2. get_for_role runs write-backfills on every read. _backfill_place_order_tasks
     re-raised the task because its dedup only looked at OPEN tasks — a completed
     one didn't count, so the PO looked "un-surfaced" again.
  3. The inbox fires several GET /tasks at once (header badge + open + completed),
     so the unguarded re-raise happened concurrently → duplicate OPEN rows.

Fix: a task explicitly completed by a user (completed_by IS NOT NULL) counts as
"already surfaced" — the backfill leaves it dismissed. System/document
completions (completed_by NULL, e.g. _complete_tasks or the stale-sweeps) still
allow a legitimate re-raise. Covers place_order / create_po / create_pa.
"""
import asyncio
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import task as task_crud
from app.crud import user as user_crud
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


def _mark_done_by_user(task: Task, user_id: uuid.UUID) -> None:
    """Mimic POST /tasks/{id}/complete — a user dismissal (completed_by set)."""
    task.is_completed = True
    task.completed_at = datetime.now(timezone.utc)
    task.completed_by = user_id


async def _rows(db, *, type_: str, doc_id: uuid.UUID) -> list[Task]:
    return list((await db.execute(
        select(Task).where(Task.type == type_, Task.document_id == doc_id)
    )).scalars().all())


# ── place_order ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_place_order_not_resurrected_after_user_marks_done(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        officer = await user_crud.create(db, RegisterRequest(
            email=f"po-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
            full_name="Proc Officer", role="procurement_officer"))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="PO Vendor",
                        category="Services", contact_name="C", contact_email="c@p.test")
        db.add(vendor)
        await db.flush()
        # approved + place_order_method NULL → the backfill's re-raise condition
        po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="PO", type=2,
                           vendor_id=vendor.id, vendor_name=vendor.name, created_by=officer.id,
                           status="approved", total=Decimal("100.00"), approval_step_idx=2)
        db.add(po)
        await db.flush()
        task = Task(type="place_order", priority="normal", document_type="po",
                    document_id=po.id, document_number=po.number,
                    assigned_role="procurement_officer", title=f"Place Order: {po.number}",
                    amount=po.total, vendor=po.vendor_name)
        db.add(task)
        await db.commit()

        _mark_done_by_user(task, officer.id)
        await db.commit()

        tasks = await task_crud.get_for_role(db, "procurement_officer", officer.id)
        await db.commit()

        surfaced = [t for t in tasks if t.type == "place_order" and t.document_id == po.id]
        assert surfaced == [], "user-dismissed place_order must not be re-raised"
        assert len(await _rows(db, type_="place_order", doc_id=po.id)) == 1, \
            "no new place_order row should be inserted (duplication bug)"


# ── create_po ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_po_not_resurrected_after_user_marks_done(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        officer = await user_crud.create(db, RegisterRequest(
            email=f"cpo-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
            full_name="Proc Officer", role="procurement_officer"))
        # approved PR with no PO → _backfill_create_po_tasks re-raise condition
        pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:6]}", title="PR", type=2,
                             status="approved", amount=Decimal("100.00"), created_by=officer.id)
        db.add(pr)
        await db.flush()
        task = Task(type="create_po", priority="normal", document_type="pr",
                    document_id=pr.id, document_number=pr.number,
                    assigned_role="procurement_officer", title=f"Create PO: {pr.number}",
                    amount=pr.amount)
        db.add(task)
        await db.commit()

        _mark_done_by_user(task, officer.id)
        await db.commit()

        tasks = await task_crud.get_for_role(db, "procurement_officer", officer.id)
        await db.commit()

        surfaced = [t for t in tasks if t.type == "create_po" and t.document_id == pr.id]
        assert surfaced == [], "user-dismissed create_po must not be re-raised"
        assert len(await _rows(db, type_="create_po", doc_id=pr.id)) == 1, \
            "no new create_po row should be inserted (duplication bug)"


# ── create_pa ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_pa_not_resurrected_after_user_marks_done(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        requester = await user_crud.create(db, RegisterRequest(
            email=f"cpa-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
            full_name="Requester", role="requester"))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="PA Vendor",
                        category="Services", contact_name="C", contact_email="c@a.test")
        db.add(vendor)
        await db.flush()
        pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:6]}", title="PR", type=2,
                             status="approved", amount=Decimal("100.00"), created_by=requester.id)
        db.add(pr)
        await db.flush()
        # payable PO, matched invoice, no PA → _backfill_create_pa_tasks re-raise condition
        po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="PO", type=2,
                           vendor_id=vendor.id, vendor_name=vendor.name, created_by=requester.id,
                           pr_id=pr.id, status="issued", total=Decimal("113.00"), approval_step_idx=2)
        db.add(po)
        await db.flush()
        pr.po_id = po.id
        inv = Invoice(internal_ref=f"IVN-{uuid.uuid4().hex[:6]}", vendor_invoice_number="V-INV",
                      vendor_id=vendor.id, vendor_name=vendor.name, amount=Decimal("100.00"),
                      total_amount=Decimal("113.00"), invoice_date=date(2026, 7, 1),
                      due_date=date(2026, 7, 31), status="matched", po_id=po.id,
                      uploaded_by=requester.id, created_at=datetime(2026, 7, 7, tzinfo=timezone.utc))
        db.add(inv)
        task = Task(type="create_pa", priority="normal", document_type="po",
                    document_id=po.id, document_number=po.number, assigned_role="requester",
                    assigned_user_id=requester.id, title=f"Create Payment Application for {po.number}",
                    amount=po.total, vendor=po.vendor_name)
        db.add(task)
        await db.commit()

        _mark_done_by_user(task, requester.id)
        await db.commit()

        tasks = await task_crud.get_for_role(db, "requester", requester.id)
        await db.commit()

        surfaced = [t for t in tasks if t.type == "create_pa" and t.document_id == po.id]
        assert surfaced == [], "user-dismissed create_pa must not be re-raised"
        assert len(await _rows(db, type_="create_pa", doc_id=po.id)) == 1, \
            "no new create_pa row should be inserted (duplication bug)"


# ── guard: a SYSTEM completion (completed_by NULL) still allows a re-raise ───────

@pytest.mark.asyncio
async def test_place_order_reraised_after_system_completion(test_engine):
    """A place_order task completed WITHOUT a user (completed_by NULL) — e.g. a
    document action or stale-sweep — does not permanently suppress the backfill.
    Guards against over-suppressing legitimate re-raises."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        officer = await user_crud.create(db, RegisterRequest(
            email=f"sys-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
            full_name="Proc Officer", role="procurement_officer"))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Sys Vendor",
                        category="Services", contact_name="C", contact_email="c@s.test")
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="PO", type=2,
                           vendor_id=vendor.id, vendor_name=vendor.name, created_by=officer.id,
                           status="approved", total=Decimal("100.00"), approval_step_idx=2)
        db.add(po)
        await db.flush()
        # completed but WITHOUT completed_by → a system completion
        task = Task(type="place_order", priority="normal", document_type="po",
                    document_id=po.id, document_number=po.number,
                    assigned_role="procurement_officer", title=f"Place Order: {po.number}",
                    amount=po.total, vendor=po.vendor_name,
                    is_completed=True, completed_at=datetime.now(timezone.utc))
        db.add(task)
        await db.commit()

        tasks = await task_crud.get_for_role(db, "procurement_officer", officer.id)
        await db.commit()

        surfaced = [t for t in tasks if t.type == "place_order" and t.document_id == po.id]
        assert len(surfaced) == 1, "system-completed place_order (approved PO) is re-raised"
        assert surfaced[0].is_completed is False


# ── concurrency: the first-time backfill must not double-insert ─────────────────

@pytest.mark.asyncio
async def test_concurrent_get_for_role_does_not_duplicate_place_order(test_engine):
    """Two GET /tasks landing at once (header badge + inbox lists) must not each
    INSERT a place_order task for the same never-tasked approved PO. The
    transaction-scoped advisory lock in get_for_role serializes the
    read-check-insert so exactly one task exists."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        officer = await user_crud.create(db, RegisterRequest(
            email=f"race-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
            full_name="Proc Officer", role="procurement_officer"))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Race Vendor",
                        category="Services", contact_name="C", contact_email="c@r.test")
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="PO", type=2,
                           vendor_id=vendor.id, vendor_name=vendor.name, created_by=officer.id,
                           status="approved", total=Decimal("100.00"), approval_step_idx=2)
        db.add(po)
        await db.commit()
        officer_id, po_id = officer.id, po.id

    async def load_inbox():
        async with factory() as s:
            await task_crud.get_for_role(s, "procurement_officer", officer_id)
            await s.commit()

    # Fire both inbox loads concurrently on the shared event loop.
    await asyncio.gather(load_inbox(), load_inbox())

    async with factory() as db:
        rows = await _rows(db, type_="place_order", doc_id=po_id)
        assert len(rows) == 1, f"expected exactly 1 place_order task, got {len(rows)}"
