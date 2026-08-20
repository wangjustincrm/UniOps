"""One-shot backfill: invoices that reached status='exception' before
resolve_exception tasks existed must get one on the first pass, must not get
a duplicate on any later pass, must leave non-exception invoices alone, and
dry-run must write nothing.

Modeled on tests/test_backfill_invoice_gr_links.py (script invoked as its
real entry point against the test DB) and seeds exception invoices the same
minimal-ORM way tests/test_invoice_exception_task.py does functionally via
the API — here directly via the model, since the backfill only cares about
Invoice.status, not how it got there.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

import app.db.session as sm
from app.crud import user as user_crud
from app.models.invoice import Invoice
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from scripts.backfill_exception_tasks import backfill

from tests.conftest import _TEST_DB_URL


async def _seed_invoice(db, user_id, vendor_id, *, status: str, ref: str):
    inv = Invoice(
        internal_ref=ref, vendor_invoice_number=ref, vendor_id=vendor_id,
        vendor_name="Acme", amount=Decimal("200"), tax_amount=Decimal("0"),
        total_amount=Decimal("200"), invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1), status=status, line_items=[],
        uploaded_by=user_id,
        exception_reason="Variance +100% exceeds tolerance." if status == "exception" else None,
    )
    db.add(inv)
    await db.flush()
    return inv


@pytest.mark.asyncio
async def test_backfill_creates_task_for_untracked_exception_invoice():
    async with sm.AsyncSessionLocal() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"bfexc-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Backfill Exc Tester", role="ap_clerk"))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
                        contact_name="C", contact_email="c@x.com")
        db.add(vendor)
        await db.flush()
        inv = await _seed_invoice(db, user.id, vendor.id, status="exception", ref=f"BFE-{uuid.uuid4().hex[:6]}")
        inv_id = inv.id
        await db.commit()

    try:
        stats = await backfill(dry_run=False, db_url=_TEST_DB_URL)
        assert stats["created"] >= 1

        async with sm.AsyncSessionLocal() as db:
            task = (await db.execute(select(Task).where(
                Task.type == "resolve_exception", Task.document_type == "invoice",
                Task.document_id == inv_id, Task.is_completed.is_(False),
            ))).scalar_one_or_none()
            assert task is not None, "backfill must raise a resolve_exception task"
            assert task.assigned_role == "ap_clerk"
            assert task.assigned_user_id is None, "must be role-pool, not user-pinned"
            assert task.document_number == inv.internal_ref
            assert task.vendor == "Acme"
            assert task.amount == Decimal("200.00")
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.document_id == inv_id))
            await db.execute(sa_delete(Invoice).where(Invoice.id == inv_id))
            await db.commit()


@pytest.mark.asyncio
async def test_backfill_is_idempotent_when_task_already_open():
    async with sm.AsyncSessionLocal() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"bfexc-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Backfill Exc Tester 2", role="ap_clerk"))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
                        contact_name="C", contact_email="c@x.com")
        db.add(vendor)
        await db.flush()
        inv = await _seed_invoice(db, user.id, vendor.id, status="exception", ref=f"BFE-{uuid.uuid4().hex[:6]}")
        existing = Task(
            type="resolve_exception", priority="normal", document_type="invoice",
            document_id=inv.id, document_number=inv.internal_ref,
            assigned_role="ap_clerk", title="Resolve match exception — pre-existing",
            vendor=inv.vendor_name, amount=inv.total_amount,
        )
        db.add(existing)
        await db.flush()
        inv_id, existing_id = inv.id, existing.id
        await db.commit()

    try:
        stats = await backfill(dry_run=False, db_url=_TEST_DB_URL)
        assert stats["already_has_task"] >= 1

        async with sm.AsyncSessionLocal() as db:
            tasks = (await db.execute(select(Task).where(
                Task.type == "resolve_exception", Task.document_id == inv_id,
            ))).scalars().all()
            assert len(tasks) == 1, "must not create a duplicate task"
            assert tasks[0].id == existing_id

        # Running it again is still a no-op.
        again = await backfill(dry_run=False, db_url=_TEST_DB_URL)
        async with sm.AsyncSessionLocal() as db:
            tasks = (await db.execute(select(Task).where(
                Task.type == "resolve_exception", Task.document_id == inv_id,
            ))).scalars().all()
            assert len(tasks) == 1
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.document_id == inv_id))
            await db.execute(sa_delete(Invoice).where(Invoice.id == inv_id))
            await db.commit()


@pytest.mark.asyncio
async def test_backfill_leaves_non_exception_invoice_untouched():
    async with sm.AsyncSessionLocal() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"bfexc-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Backfill Exc Tester 3", role="ap_clerk"))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
                        contact_name="C", contact_email="c@x.com")
        db.add(vendor)
        await db.flush()
        inv = await _seed_invoice(db, user.id, vendor.id, status="matched", ref=f"BFM-{uuid.uuid4().hex[:6]}")
        inv_id = inv.id
        await db.commit()

    try:
        await backfill(dry_run=False, db_url=_TEST_DB_URL)

        async with sm.AsyncSessionLocal() as db:
            task = (await db.execute(select(Task).where(
                Task.document_id == inv_id,
            ))).scalar_one_or_none()
            assert task is None, "non-exception invoices must not get a task"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Invoice).where(Invoice.id == inv_id))
            await db.commit()


@pytest.mark.asyncio
async def test_backfill_dry_run_writes_nothing():
    async with sm.AsyncSessionLocal() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"bfexc-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Backfill Exc Tester 4", role="ap_clerk"))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
                        contact_name="C", contact_email="c@x.com")
        db.add(vendor)
        await db.flush()
        inv = await _seed_invoice(db, user.id, vendor.id, status="exception", ref=f"BFE-{uuid.uuid4().hex[:6]}")
        inv_id = inv.id
        await db.commit()

    try:
        stats = await backfill(dry_run=True, db_url=_TEST_DB_URL)
        assert stats["created"] >= 1, "dry-run must still report what it WOULD do"

        async with sm.AsyncSessionLocal() as db:
            task = (await db.execute(select(Task).where(
                Task.document_id == inv_id,
            ))).scalar_one_or_none()
            assert task is None, "dry-run must not write"
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Invoice).where(Invoice.id == inv_id))
            await db.commit()
