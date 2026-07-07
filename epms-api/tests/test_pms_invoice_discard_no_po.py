"""PMS import rule: discard an INVOICE whose ID is not referenced by any PO Item
(or PA Item) InvoiceID — i.e. it has no PO link at all. A referenced invoice is
still imported.
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

PO_NUM = "PO-DISCARD-1"
LINKED_IID = "9001"      # referenced by a po_item → keep
ORPHAN_IID = "9002"      # referenced by nothing → discard

PHANTOM_IID = "9003"     # po_item points at a PO in neither EPMS nor po.json → discard

_STAGING = {
    "po_item.json": [
        {"ID": "1", "Title": PO_NUM, "InvoiceID": LINKED_IID,
         "Description": "w", "QTY": "1", "UOM": "EA", "UnitPrice": "50", "TotalPrice": "50"},
        {"ID": "2", "Title": "PO-PHANTOM-99", "InvoiceID": PHANTOM_IID, "TotalPrice": "10"},
    ],
    "invoice.json": [
        {"ID": LINKED_IID, "Title": "V-LINKED", "PONo": "",
         "IssueDate": "2024-01-01", "Created": "2024-01-01T00:00:00Z", "Modified": "2024-01-01T00:00:00Z"},
        {"ID": ORPHAN_IID, "Title": "V-ORPHAN", "PONo": "",
         "IssueDate": "2024-01-01", "Created": "2024-01-01T00:00:00Z", "Modified": "2024-01-01T00:00:00Z"},
        {"ID": PHANTOM_IID, "Title": "V-PHANTOM", "PONo": "",
         "IssueDate": "2024-01-01", "Created": "2024-01-01T00:00:00Z", "Modified": "2024-01-01T00:00:00Z"},
    ],
}


@pytest.fixture
def fake_staging(monkeypatch):
    monkeypatch.setattr(load, "load_staging", lambda fn: [dict(x) for x in _STAGING.get(fn, [])])


async def _seed_po(test_engine) -> uuid.UUID:
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = User(id=uuid.uuid4(), email=f"u-{uuid.uuid4().hex[:8]}@example.com",
                    hashed_password=hash_password("x"), full_name="U", role="requester", is_active=True)
        vendor = Vendor(id=uuid.uuid4(), code=f"V{uuid.uuid4().hex[:6]}", name="Acme",
                        category="general", contact_name="N/A", contact_email="v@example.com", payment_terms="net30")
        db.add_all([user, vendor])
        await db.flush()
        db.add(PurchaseOrder(id=uuid.uuid4(), number=PO_NUM, title=PO_NUM, type=2,
                             vendor_id=vendor.id, vendor_name=vendor.name, created_by=user.id,
                             total=Decimal("50"), subtotal=Decimal("50")))
        await db.commit()
        return vendor.id


async def test_orphan_invoice_discarded_linked_kept(test_engine, fake_staging):
    vendor_id = await _seed_po(test_engine)
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        report = await load.run_load(
            dry_run=False, mode="upsert", db_url=_TEST_DB_URL,
            reconstruct=False, dedup_invoices=False,
        )
        # ORPHAN (no reference) + PHANTOM (references a non-existent PO) both dropped.
        assert report.invoices_discarded_no_po == 2

        all_refs = [f"INV-{LINKED_IID}", f"INV-{ORPHAN_IID}", f"INV-{PHANTOM_IID}"]
        async with sf() as db:
            refs = set((await db.execute(
                select(Invoice.internal_ref).where(Invoice.internal_ref.in_(all_refs))
            )).scalars().all())
        assert f"INV-{LINKED_IID}" in refs, "invoice referenced by a real PO must be imported"
        assert f"INV-{ORPHAN_IID}" not in refs, "invoice with no PO/PA link must be discarded"
        assert f"INV-{PHANTOM_IID}" not in refs, "invoice referencing a phantom PO must be discarded"
    finally:
        async with sf() as db:
            await db.execute(sa_delete(Invoice).where(Invoice.internal_ref.in_(
                [f"INV-{LINKED_IID}", f"INV-{ORPHAN_IID}", f"INV-{PHANTOM_IID}"])))
            await db.execute(sa_delete(PurchaseOrder).where(PurchaseOrder.number == PO_NUM))
            await db.execute(sa_delete(Vendor).where(Vendor.id == vendor_id))
            await db.commit()
