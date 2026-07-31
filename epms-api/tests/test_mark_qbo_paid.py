"""Tests for scripts/mark_qbo_paid_processed.py — QBO-paid PA reconciliation.

Reflects a QBO-already-paid approved PA as 'processed' WITHOUT re-booking:
status/paid_at/invoice/tasks flip, and NO posting is emitted. Idempotent, and
only acts on PAs currently 'approved'.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.db.session as session_module
from app.crud import user as user_crud
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from scripts.mark_qbo_paid_processed import _ap_invoices_present, reflect_one


async def _seed(db, *, status="approved", pa_type="regular", inv_status="matched", with_task=True):
    u = await user_crud.create(db, RegisterRequest(
        email=f"qbo-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Recon Tester", role="requester"))
    await db.commit()
    v = Vendor(id=uuid.uuid4(), code=f"V-{uuid.uuid4().hex[:8]}", name="Acme",
               category="supplier", contact_name="C", contact_email="c@acme.com")
    db.add(v)
    await db.flush()
    inv = Invoice(
        id=uuid.uuid4(), internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
        vendor_invoice_number="6645595", vendor_id=v.id, vendor_name="Acme",
        amount=Decimal("92.00"), total_amount=Decimal("92.00"),
        invoice_date=date(2025, 11, 26), due_date=date(2025, 12, 26),
        status=inv_status, uploaded_by=u.id,
    )
    db.add(inv)
    await db.flush()
    pa = PaymentApplication(
        pa_number=f"PA-QBO-{uuid.uuid4().hex[:6]}", title="recon test", status=status,
        pa_type=pa_type, subtotal=Decimal("92.00"), payment_amount=Decimal("92.00"),
        vendor_id=v.id, vendor_name="Acme", created_by=u.id, invoice_ids=[str(inv.id)],
    )
    db.add(pa)
    await db.flush()
    if with_task:
        db.add(Task(type="approve_pa", document_type="pa", document_id=pa.id,
                    document_number=pa.pa_number, assigned_role="finance_manager", title="x"))
        await db.flush()
    return pa, inv


async def test_reflect_marks_processed_with_qbo_date_and_closes():
    async with session_module.AsyncSessionLocal() as db:
        pa, inv = await _seed(db)
        ap_present = await _ap_invoices_present(db)  # False in epms_test (no finance table)

        outcome = await reflect_one(db, pa.pa_number, "2025-12-02", ap_present)

        assert outcome == "processed"
        await db.refresh(pa); await db.refresh(inv)
        assert pa.status == "processed"
        assert pa.paid_at == datetime(2025, 12, 2, tzinfo=timezone.utc)  # QBO date, not now
        assert inv.status == "paid"
        open_tasks = (await db.execute(select(Task).where(
            Task.document_id == pa.id, Task.is_completed.is_(False)))).scalars().all()
        assert open_tasks == []  # approval task closed
        await db.rollback()


async def test_idempotent_already_processed():
    async with session_module.AsyncSessionLocal() as db:
        pa, _ = await _seed(db, status="processed")
        outcome = await reflect_one(db, pa.pa_number, "2025-12-02", False)
        assert outcome == "already_processed"
        await db.refresh(pa)
        assert pa.paid_at is None  # untouched
        await db.rollback()


async def test_non_approved_is_skipped():
    async with session_module.AsyncSessionLocal() as db:
        pa, inv = await _seed(db, status="in_review")
        outcome = await reflect_one(db, pa.pa_number, "2025-12-02", False)
        assert outcome == "skip_status:in_review"
        await db.refresh(pa); await db.refresh(inv)
        assert pa.status == "in_review"  # unchanged
        assert inv.status == "matched"
        await db.rollback()


async def test_prepayment_leaves_invoice_partially_paid():
    async with session_module.AsyncSessionLocal() as db:
        pa, inv = await _seed(db, pa_type="prepayment")
        outcome = await reflect_one(db, pa.pa_number, "2025-12-02", False)
        assert outcome == "processed"
        await db.refresh(inv)
        assert inv.status == "partially_paid"
        await db.rollback()
