"""Task 7 (Part A): `source` must be visible on the PO/GR read schemas so the
frontend can render a read-only "NC" badge on mirrored documents.

Creates a PO/GR directly via the ORM with source='nc' (the same provenance
field the NC purchase sync writer sets — app/services/nc_purchase_sync/writer.py)
and asserts the GET /po/{id} and GET /gr/{id} JSON responses surface it.
"""
import uuid

import pytest

import app.db.session as sm
from app.crud import user as user_crud
from app.models.gr import GoodsReceipt
from app.models.po import PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


async def _setup(db):
    user = await user_crud.create(db, RegisterRequest(
        email=f"nc-src-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="NC Source Test User", role="requester"))
    v = Vendor(code=f"V-NC-{uuid.uuid4().hex[:8]}", name="NC Vendor", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v); await db.flush()
    return user, v


@pytest.mark.asyncio
async def test_po_read_schema_exposes_nc_source(admin_client):
    async with sm.AsyncSessionLocal() as db:
        user, v = await _setup(db)
        po = PurchaseOrder(
            number=f"PO-NC-{uuid.uuid4().hex[:8]}", title="Mirrored from NC", type=1,
            vendor_id=v.id, vendor_name=v.name, status="issued",
            created_by=user.id, source="nc", nc_source_pk="O123",
        )
        db.add(po); await db.commit()
        po_id = po.id

    resp = await admin_client.get(f"/api/v1/po/{po_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "nc"


@pytest.mark.asyncio
async def test_po_read_schema_source_none_for_native_po(admin_client):
    async with sm.AsyncSessionLocal() as db:
        user, v = await _setup(db)
        po = PurchaseOrder(
            number=f"PO-NAT-{uuid.uuid4().hex[:8]}", title="Native PO", type=2,
            vendor_id=v.id, vendor_name=v.name, status="draft", created_by=user.id,
        )
        db.add(po); await db.commit()
        po_id = po.id

    resp = await admin_client.get(f"/api/v1/po/{po_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] is None


@pytest.mark.asyncio
async def test_gr_read_schema_exposes_nc_source(admin_client):
    async with sm.AsyncSessionLocal() as db:
        user, v = await _setup(db)
        po = PurchaseOrder(
            number=f"PO-NC-{uuid.uuid4().hex[:8]}", title="Mirrored from NC", type=1,
            vendor_id=v.id, vendor_name=v.name, status="issued",
            created_by=user.id, source="nc", nc_source_pk="O124",
        )
        db.add(po); await db.flush()
        gr = GoodsReceipt(
            number=f"GR-NC-{uuid.uuid4().hex[:8]}", title="Mirrored arrival", po_id=po.id,
            po_number=po.number, vendor_id=v.id, vendor_name=v.name,
            gr_type="physical", procurement_type=1, status="collected",
            created_by=user.id, source="nc", nc_source_pk="A1:O124",
        )
        db.add(gr); await db.commit()
        gr_id = gr.id

    resp = await admin_client.get(f"/api/v1/gr/{gr_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "nc"
