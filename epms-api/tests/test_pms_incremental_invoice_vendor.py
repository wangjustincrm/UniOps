"""Incremental PMS import: an invoice tied to an already-imported PO must inherit
that PO's vendor + po_id — not fall back to "PMS Unknown Vendor".

Regression: extract filtered po_item/pa_item by Modified, and the invoice loader
resolved vendor/po only from POs loaded in the same batch. So an incremental
invoice pointing at an unchanged PO lost its vendor. Fix = full-pull the linkage
tables + seed pono_to_id/pono_to_vendor from the DB for POs not in the batch.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from app.models.user import User
from app.models.vendor import Vendor
from scripts.import_pms import load
from tests.conftest import _TEST_DB_URL

PO_NUM = "PO-INCR-1"
SP_INVOICE_ID = "9001"

# po_item carries the ONLY invoice→PO link (InvoiceID + Title=PO number); po.json
# is empty so the PO is "not in this batch" — the exact incremental scenario.
_STAGING = {
    "po_item.json": [{
        "ID": "1", "Title": PO_NUM, "InvoiceID": SP_INVOICE_ID,
        "Description": "widget", "QTY": "1", "UOM": "EA",
        "UnitPrice": "100", "TotalPrice": "100",
    }],
    "invoice.json": [{
        "ID": SP_INVOICE_ID, "Title": "VINV-1", "PONo": "",
        "IssueDate": "2024-01-01", "Created": "2024-01-01T00:00:00Z",
        "Modified": "2024-01-01T00:00:00Z",
    }],
}


@pytest.fixture
def fake_staging(monkeypatch):
    monkeypatch.setattr(load, "load_staging", lambda fn: [dict(x) for x in _STAGING.get(fn, [])])


async def _seed_existing_po(test_engine) -> tuple[uuid.UUID, uuid.UUID]:
    """A vendor + an already-imported PO (as if from the earlier full run)."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = User(
            id=uuid.uuid4(), email=f"po-owner-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password=hash_password("x"), full_name="PO Owner",
            role="requester", is_active=True,
        )
        vendor = Vendor(
            id=uuid.uuid4(), code=f"V{uuid.uuid4().hex[:6]}", name="Acme Corp",
            category="general", contact_name="N/A",
            contact_email="v@example.com", payment_terms="net30",
        )
        db.add_all([user, vendor])
        await db.flush()
        po = PurchaseOrder(
            id=uuid.uuid4(), number=PO_NUM, title=PO_NUM, type=2,
            vendor_id=vendor.id, vendor_name=vendor.name, created_by=user.id,
            total=Decimal("100"), subtotal=Decimal("100"),
        )
        db.add(po)
        await db.commit()
        return vendor.id, po.id


async def test_incremental_invoice_inherits_existing_po_vendor(test_engine, fake_staging):
    vendor_id, po_id = await _seed_existing_po(test_engine)
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        # Incremental load — PO not in the batch, only its po_item linkage is.
        await load.run_load(
            dry_run=False, mode="upsert", db_url=_TEST_DB_URL,
            reconstruct=False, dedup_invoices=False,
        )

        async with sf() as db:
            inv = (await db.execute(
                select(Invoice).where(Invoice.internal_ref == f"INV-{SP_INVOICE_ID}")
            )).scalar_one()

        assert inv.vendor_id == vendor_id, "invoice must inherit the existing PO's vendor"
        assert inv.vendor_name == "Acme Corp"
        assert inv.po_id == po_id, "invoice must link to the existing PO"
    finally:
        # run_load commits its own transaction; clean up so the shared session DB
        # isn't polluted for other tests in the same run.
        async with sf() as db:
            await db.execute(sa_delete(Invoice).where(Invoice.internal_ref == f"INV-{SP_INVOICE_ID}"))
            await db.execute(sa_delete(PurchaseOrder).where(PurchaseOrder.number == PO_NUM))
            await db.execute(sa_delete(Vendor).where(Vendor.id == vendor_id))
            await db.commit()
