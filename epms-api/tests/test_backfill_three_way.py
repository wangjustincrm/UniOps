"""_backfill_create_pa_tasks 只回填 3-way(matched + gr_id) 的 PO。"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.db.session as sm
from app.crud import user as user_crud
from app.crud.task import _backfill_create_pa_tasks
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _po_with_matched_invoice(db, *, with_gr, source=None, pr_id=None):
    u = await user_crud.create(db, RegisterRequest(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="U", role="requester"))
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v); await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=2,
                       vendor_id=v.id, vendor_name="Acme", status="issued",
                       created_by=u.id, source=source, pr_id=pr_id)
    db.add(po); await db.flush()
    gr_id = None
    if with_gr:
        gr = GoodsReceipt(number=f"GR-{uuid.uuid4().hex[:8]}", title="G", po_id=po.id,
                          po_number=po.number, vendor_id=v.id, vendor_name="Acme",
                          gr_type="physical", procurement_type=2, status="collected", created_by=u.id)
        db.add(gr); await db.flush()
        gr_id = gr.id
    inv = Invoice(internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
                  vendor_id=v.id, vendor_name="Acme", amount=Decimal("100"), tax_amount=Decimal("0"),
                  total_amount=Decimal("100"), invoice_date=date(2026,1,1), due_date=date(2026,2,1),
                  status="matched", line_items=[], po_id=po.id, gr_id=gr_id, uploaded_by=u.id)
    db.add(inv); await db.flush()
    return po


async def _open_create_pa(db, po_id):
    return (await db.execute(select(Task).where(
        Task.type == "create_pa", Task.document_id == po_id,
        Task.is_completed.is_(False)))).scalars().all()


@pytest.mark.asyncio
async def test_backfill_skips_two_way_only_po():
    async with sm.AsyncSessionLocal() as db:
        po = await _po_with_matched_invoice(db, with_gr=False)   # 2-way(无 GR)
        await _backfill_create_pa_tasks(db)
        await db.flush()
        assert await _open_create_pa(db, po.id) == []


@pytest.mark.asyncio
async def test_backfill_creates_for_three_way_po():
    async with sm.AsyncSessionLocal() as db:
        po = await _po_with_matched_invoice(db, with_gr=True)    # 3-way(挂 GR)
        await _backfill_create_pa_tasks(db)
        await db.flush()
        tasks = await _open_create_pa(db, po.id)
        assert len(tasks) == 1
        assert tasks[0].assigned_role == "requester"            # non-NC → requester
        assert tasks[0].assigned_user_id == po.created_by


@pytest.mark.asyncio
async def test_backfill_routes_nc_po_to_erp_pa_officer_pool():
    """A 3-way NC-imported PO (no PR) backfills a create_pa task to the
    erp_pa_officer pool (broadcast, NULL assignee), not the nc-sync PO creator."""
    async with sm.AsyncSessionLocal() as db:
        po = await _po_with_matched_invoice(db, with_gr=True, source="nc")
        await _backfill_create_pa_tasks(db)
        await db.flush()
        tasks = await _open_create_pa(db, po.id)
        assert len(tasks) == 1
        assert tasks[0].assigned_role == "erp_pa_officer"
        assert tasks[0].assigned_user_id is None
