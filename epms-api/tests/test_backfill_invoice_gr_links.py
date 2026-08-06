"""The one-shot backfill links the invoices that predate the two hook fixes.

Runs the real script entry point against the test DB (its own engine/session, so
the fixture rows have to be committed) and checks both directions it repairs:
a header-level (total-value) match and a match_review invoice whose GR arrived
afterwards. Dry-run must change nothing.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

import app.db.session as sm
from app.crud import user as user_crud
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.po import PoLineItem, PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from scripts.backfill_invoice_gr_links import backfill

from tests.conftest import _TEST_DB_URL


async def _seed(db):
    user = await user_crud.create(db, RegisterRequest(
        email=f"bf-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Backfill Tester", role="ap_clerk",
    ))
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
                    contact_name="C", contact_email="c@x.com")
    db.add(vendor)
    await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="T", type=2,
                       vendor_id=vendor.id, vendor_name="Acme", status="issued",
                       subtotal=Decimal("50"), created_by=user.id)
    db.add(po)
    await db.flush()
    line = PoLineItem(po_id=po.id, description="A", qty=Decimal("10"), unit="ea",
                      unit_price=Decimal("5"), line_total=Decimal("50"))
    db.add(line)
    await db.flush()

    gr = GoodsReceipt(number=f"GR-{uuid.uuid4().hex[:8]}", title="Goods", po_id=po.id,
                      po_number=po.number, vendor_id=vendor.id, vendor_name="Acme",
                      gr_type="physical", procurement_type=2, status="collected",
                      created_by=user.id)
    db.add(gr)
    await db.flush()
    db.add(GrLineItem(gr_id=gr.id, po_line_id=line.id, description="widget",
                      qty_ordered=Decimal("10"), qty_received=Decimal("10"), unit="ea",
                      unit_price=Decimal("5"), line_total=Decimal("50")))

    made = {}
    for key, status, po_line_id in (("header", "matched", None),
                                    ("review", "match_review", line.id)):
        ref = f"BF{key[:1].upper()}-{uuid.uuid4().hex[:6]}"
        inv = Invoice(internal_ref=ref, vendor_invoice_number=ref, vendor_id=vendor.id,
                      vendor_name="Acme", amount=Decimal("50"), tax_amount=Decimal("0"),
                      total_amount=Decimal("50"), invoice_date=date(2026, 1, 1),
                      due_date=date(2026, 2, 1), status=status, line_items=[],
                      po_id=po.id, po_number=po.number, uploaded_by=user.id)
        db.add(inv)
        await db.flush()
        db.add(InvoicePoAllocation(
            invoice_id=inv.id, invoice_line_id=uuid.uuid4(), po_id=po.id,
            po_line_id=po_line_id, allocated_amount=Decimal("50"),
            allocated_total=Decimal("50"),
        ))
        made[key] = inv
    await db.flush()
    return gr, made


@pytest.mark.asyncio
async def test_backfill_dry_run_changes_nothing_then_apply_links():
    async with sm.AsyncSessionLocal() as db:
        gr, made = await _seed(db)
        ids = {k: v.id for k, v in made.items()}
        await db.commit()

    try:
        stats = await backfill(dry_run=True, db_url=_TEST_DB_URL)
        assert stats["linked"] >= 2

        async with sm.AsyncSessionLocal() as db:
            for inv_id in ids.values():
                inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
                assert inv.gr_id is None, "dry-run must not write"

        await backfill(dry_run=False, db_url=_TEST_DB_URL)

        async with sm.AsyncSessionLocal() as db:
            for inv_id in ids.values():
                inv = (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
                assert inv.gr_id == gr.id
                assert inv.gr_ids == [str(gr.id)]
                assert inv.gr_value == Decimal("50.00")

        # Idempotent: nothing left to link on a second pass.
        again = await backfill(dry_run=True, db_url=_TEST_DB_URL)
        assert again["linked"] == 0
    finally:
        async with sm.AsyncSessionLocal() as db:
            await db.execute(sa_delete(InvoicePoAllocation).where(
                InvoicePoAllocation.invoice_id.in_(list(ids.values()))))
            await db.execute(sa_delete(Invoice).where(Invoice.id.in_(list(ids.values()))))
            await db.commit()
