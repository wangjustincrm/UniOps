"""ISS-0087: a PO's payment terms — defaulted from the vendor, overridable
(PRD §3.12.1)."""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.po import PurchaseOrder
from tests.test_invoice_allocations import _ensure_company_config

PO_URL = "/api/v1/po"
VENDOR_URL = "/api/v1/vendors"
_LINE = {"description": "Widget", "qty": "1", "unit": "EA", "unit_price": "10.00"}


async def _vendor(client, terms):
    r = await client.post(VENDOR_URL, json={
        "code": f"PT-{uuid.uuid4().hex[:8].upper()}", "name": f"Terms Vendor {uuid.uuid4().hex[:6]}",
        "category": "Parts", "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": terms, "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _po(client, vendor_id, **extra):
    return await client.post(PO_URL, json={
        "title": "Terms PO", "type": 2, "vendor_id": vendor_id, "currency": "CAD",
        "line_items": [_LINE], **extra,
    })


@pytest.mark.asyncio
async def test_po_terms_default_from_the_vendor_and_can_be_overridden(admin_client):
    v = await _vendor(admin_client, "net45")

    r = await _po(admin_client, v["id"])
    assert r.status_code == 201, r.text
    assert r.json()["payment_terms"] == "net45"            # defaulted from the vendor

    r = await _po(admin_client, v["id"], payment_terms="Net 60")
    assert r.status_code == 201, r.text
    po = r.json()
    assert po["payment_terms"] == "net60"                  # overridden, normalised

    r = await admin_client.patch(f"{PO_URL}/{po['id']}", json={"payment_terms": "cod"})
    assert r.status_code == 200, r.text
    assert r.json()["payment_terms"] == "cod"

    r = await _po(admin_client, v["id"], payment_terms="net0")
    assert r.status_code == 422, r.text                    # same rule as the vendor's terms


@pytest.mark.asyncio
async def test_a_po_without_its_own_terms_reads_the_vendors(admin_client, test_engine):
    await _ensure_company_config()
    v = await _vendor(admin_client, "net21")
    po = (await _po(admin_client, v["id"])).json()
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:   # a PO saved before POs had terms (or an NC mirror)
        row = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == uuid.UUID(po["id"])))).scalar_one()
        row.payment_terms = None
        await db.commit()
    d = (await admin_client.get(f"{PO_URL}/{po['id']}")).json()
    assert d["payment_terms"] is None
    assert d["vendor_payment_terms"] == "net21"
