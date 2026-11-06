"""Placing an order must close only the place_order task (ISS-0135).

PRD §3.7.1 task table: create_prepayment_pa closes when "A prepayment PA
exists". place_order() used to complete EVERY open task on the PO, so the
requester's Create Prepayment PA reminder vanished into Completed the moment
Procurement placed the order — with no PA on the PO at all.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import po as po_crud
from app.crud import task as task_crud
from app.crud import user as user_crud
from app.models.po import PurchaseOrder
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.schemas.po import PlaceOrderRequest


@pytest.mark.asyncio
async def test_place_order_keeps_create_prepayment_pa_task_open(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        officer = await user_crud.create(db, RegisterRequest(
            email=f"po-{uuid.uuid4().hex[:8]}@t.com", password="TestPass1!",
            full_name="Proc Officer", role="procurement_officer"))
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Prepay Vendor",
                        category="Services", contact_name="C", contact_email="c@p.test")
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:6]}", title="Prepay PO", type=2,
                           vendor_id=vendor.id, vendor_name=vendor.name, created_by=officer.id,
                           status="approved", is_prepaid=True,
                           total=Decimal("100.00"), approval_step_idx=2)
        db.add(po)
        await db.flush()
        # What PO approval creates for a prepaid PO (PP-014).
        await po_crud._create_place_order_task(db, po)
        await po_crud._create_prepayment_pa_task(db, po)
        await db.commit()

        await po_crud.place_order(db, po, PlaceOrderRequest(method="online"),
                                  officer.id, "procurement_officer")
        await db.commit()

        rows = (await db.execute(
            Task.__table__.select().where(Task.document_id == po.id)
        )).mappings().all()
        by_type = {r["type"]: r for r in rows}
        assert by_type["place_order"]["is_completed"] is True
        assert by_type["create_prepayment_pa"]["is_completed"] is False, (
            "placing the order must not close Create Prepayment PA — no prepayment PA exists"
        )

        # The inbox self-heal must not close it either (no prepayment PA yet).
        tasks = await task_crud.get_for_role(db, "requester", officer.id)
        await db.commit()
        assert any(t.type == "create_prepayment_pa" and t.document_id == po.id
                   and not t.is_completed for t in tasks)
