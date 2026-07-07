"""Regression: a po_item row with a blank Title (PO number) must not poison the
invoice→PO mapping. If the FIRST po_item referencing an invoice has an empty
Title, setdefault used to lock inv_pono[iid]='' → the invoice resolved to
"PMS Unknown Vendor" even though a LATER po_item row carried the real PO number.
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

PO_NUM = "PO-EMPTYTITLE-1"
IID = "9001"

# Two po_item rows for the same invoice: the FIRST has a blank Title, the second
# carries the real PO number. Order matters (setdefault takes the first).
_STAGING = {
    "po_item.json": [
        {"ID": "1", "Title": "", "InvoiceID": IID, "TotalPrice": "0"},
        {"ID": "2", "Title": PO_NUM, "InvoiceID": IID,
         "Description": "w", "QTY": "1", "UOM": "EA", "UnitPrice": "50", "TotalPrice": "50"},
    ],
    "invoice.json": [
        {"ID": IID, "Title": "V-INV", "PONo": "",
         "IssueDate": "2024-01-01", "Created": "2024-01-01T00:00:00Z", "Modified": "2024-01-01T00:00:00Z"},
    ],
}


@pytest.fixture
def fake_staging(monkeypatch):
    monkeypatch.setattr(load, "load_staging", lambda fn: [dict(x) for x in _STAGING.get(fn, [])])


async def test_blank_potitle_row_does_not_poison_vendor(test_engine, fake_staging):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = User(id=uuid.uuid4(), email=f"u-{uuid.uuid4().hex[:8]}@example.com",
                    hashed_password=hash_password("x"), full_name="U", role="requester", is_active=True)
        vendor = Vendor(id=uuid.uuid4(), code=f"V{uuid.uuid4().hex[:6]}", name="RealVendor",
                        category="general", contact_name="N/A", contact_email="v@example.com", payment_terms="net30")
        db.add_all([user, vendor])
        await db.flush()
        vendor_id, po_id = vendor.id, uuid.uuid4()
        db.add(PurchaseOrder(id=po_id, number=PO_NUM, title=PO_NUM, type=2,
                             vendor_id=vendor_id, vendor_name=vendor.name, created_by=user.id,
                             total=Decimal("50"), subtotal=Decimal("50")))
        await db.commit()
    try:
        await load.run_load(dry_run=False, mode="upsert", db_url=_TEST_DB_URL,
                            reconstruct=False, dedup_invoices=False)
        async with sf() as db:
            inv = (await db.execute(
                select(Invoice).where(Invoice.internal_ref == f"INV-{IID}"))).scalar_one()
        assert inv.vendor_id == vendor_id, "blank-Title row must not force the invoice to unknown vendor"
        assert inv.vendor_name == "RealVendor"
        assert inv.po_id == po_id
    finally:
        async with sf() as db:
            await db.execute(sa_delete(Invoice).where(Invoice.internal_ref == f"INV-{IID}"))
            await db.execute(sa_delete(PurchaseOrder).where(PurchaseOrder.number == PO_NUM))
            await db.execute(sa_delete(Vendor).where(Vendor.id == vendor_id))
            await db.commit()
