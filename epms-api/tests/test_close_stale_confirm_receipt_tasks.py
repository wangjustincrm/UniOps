"""The one-shot repair closes only the催收货 tasks that already have receipt.

Runs the real script entry point against the test DB (its own engine/session, so
the fixture rows have to be committed). Three POs, one of each shape the
production scan found:

  · received via a GR document        → close
  · received only via received_qty    → close (PMS-migrated: no GR row exists)
  · nothing received at all           → KEEP, the nudge is still valid

Dry-run must change nothing, and a second pass must find nothing left to do.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

import app.db.session as sm
from app.crud import user as user_crud
from app.models.gr import GoodsReceipt
from app.models.po import PoLineItem, PurchaseOrder
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from scripts.close_stale_confirm_receipt_tasks import close_stale

from tests.conftest import _TEST_DB_URL


async def _seed(db):
    user = await user_crud.create(db, RegisterRequest(
        email=f"cs-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Cleanup Tester", role="ap_clerk",
    ))
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
                    contact_name="C", contact_email="c@x.com")
    db.add(vendor)
    await db.flush()

    made: dict[str, Task] = {}
    for key, received in (("gr", "0"), ("qty_only", "5"), ("nothing", "0")):
        po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=2,
                           vendor_id=vendor.id, vendor_name="Acme", status="issued",
                           subtotal=Decimal("50"), created_by=user.id)
        db.add(po)
        await db.flush()
        db.add(PoLineItem(po_id=po.id, description="A", qty=Decimal("10"), unit="ea",
                          unit_price=Decimal("5"), line_total=Decimal("50"),
                          received_qty=Decimal(received)))
        if key == "gr":
            db.add(GoodsReceipt(number=f"GR-{uuid.uuid4().hex[:8]}", title="Goods",
                                po_id=po.id, po_number=po.number, vendor_id=vendor.id,
                                vendor_name="Acme", gr_type="physical",
                                procurement_type=2, status="collected",
                                created_by=user.id))
        task = Task(type="confirm_receipt", priority="normal", document_type="po",
                    document_id=po.id, document_number=po.number,
                    assigned_role="warehouse_staff",
                    title=f"Confirm goods receipt for {po.number}", vendor="Acme")
        db.add(task)
        await db.flush()
        made[key] = task
    return made


@pytest.mark.asyncio
async def test_dry_run_changes_nothing_then_apply_closes_only_received_pos():
    async with sm.AsyncSessionLocal() as db:
        made = await _seed(db)
        ids = {k: t.id for k, t in made.items()}
        await db.commit()

    try:
        stats = await close_stale(dry_run=True, db_url=_TEST_DB_URL)
        assert stats["closed"] >= 2

        async with sm.AsyncSessionLocal() as db:
            for task_id in ids.values():
                t = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
                assert t.is_completed is False, "dry-run must not write"

        await close_stale(dry_run=False, db_url=_TEST_DB_URL)

        async with sm.AsyncSessionLocal() as db:
            for key in ("gr", "qty_only"):
                t = (await db.execute(select(Task).where(Task.id == ids[key]))).scalar_one()
                assert t.is_completed is True, f"{key}: 已收货的应当被关掉"
                assert t.completed_at is not None
            t = (await db.execute(select(Task).where(Task.id == ids["nothing"]))).scalar_one()
            assert t.is_completed is False, "一件都没收的 PO,催办仍然有效,不能关"

        again = await close_stale(dry_run=True, db_url=_TEST_DB_URL)
        assert again["closed"] == 0          # 幂等
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(Task).where(Task.id.in_(list(ids.values()))))
            await db.commit()
