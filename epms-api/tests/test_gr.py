"""Goods Receipt endpoint tests."""
import pytest

GR_URL = "/api/v1/gr"
PO_URL = "/api/v1/po"
VENDOR_URL = "/api/v1/vendors"

_PO_LINE = {"description": "Hydraulic Pump", "qty": "2", "unit": "EA", "unit_price": "350.00"}


async def _make_issued_po(client, vendor_code):
    """Create a vendor + issued PO for GR tests."""
    v = await client.post(VENDOR_URL, json={
        "code": vendor_code, "name": "GR Vendor", "category": "Parts",
        "contact_name": "Alice", "contact_email": "alice@vendor.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    v.raise_for_status()
    vid = v.json()["id"]

    po = await client.post(PO_URL, json={
        "title": "GR Test PO", "type": 3, "vendor_id": vid,
        "currency": "CAD", "tax_rate": "0", "line_items": [_PO_LINE],
    })
    po.raise_for_status()
    po_id = po.json()["id"]
    po_number = po.json()["number"]

    # submit → approve step 0 → approve step 1 → issue
    for act in ["submit", "approve", "approve", "issue"]:
        r = await client.post(f"{PO_URL}/{po_id}/action", json={"action": act})
        r.raise_for_status()

    return po.json(), vid


def _gr_payload(po_id, **overrides):
    base = {
        "po_id": po_id,
        "title": "Hydraulic Pump Delivery",
        "currency": "CAD",
        "storage_location": "Warehouse A",
        "line_items": [{
            "description": "Hydraulic Pump",
            "qty_ordered": "2",
            "qty_received": "2",
            "unit": "EA",
            "unit_price": "350.00",
            "condition": "good",
        }],
    }
    base.update(overrides)
    return base


async def _create_gr(client, po_id, **overrides):
    resp = await client.post(GR_URL, json=_gr_payload(po_id, **overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Basic CRUD ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_gr(admin_client):
    po, _ = await _make_issued_po(admin_client, "VND-GR-CREATE-01")
    gr = await _create_gr(admin_client, po["id"])
    assert gr["number"].startswith("GR-")
    assert gr["status"] == "pending_ack"
    assert gr["gr_type"] == "physical"   # type=3 (Spare Parts) → physical
    assert len(gr["line_items"]) == 1


@pytest.mark.asyncio
async def test_create_gr_with_negative_discount_line(admin_client, test_engine):
    """PO 放宽了负单价折扣行(cad9d17),GR 镜像同一 PO 行时必须同样放行,
    否则仓库对带折扣行的 PO 无法收货(生产复现:POST /gr 422)。
    PO 直接在库里翻成 issued,避免依赖本地不可用的 approval-api。"""
    import uuid as _uuid
    from sqlalchemy import select as _select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models.po import PurchaseOrder

    v = await admin_client.post(VENDOR_URL, json={
        "code": "VND-GR-NEG-01", "name": "GR Vendor", "category": "Parts",
        "contact_name": "Alice", "contact_email": "alice@vendor.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    v.raise_for_status()
    po = await admin_client.post(PO_URL, json={
        "title": "Discounted PO", "type": 3, "vendor_id": v.json()["id"],
        "currency": "CAD", "tax_rate": "0",
        "line_items": [
            _PO_LINE,
            {"description": "30% Discount", "qty": "1", "unit": "EA", "unit_price": "-100.00"},
        ],
    })
    po.raise_for_status()
    po_id = po.json()["id"]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(
            _select(PurchaseOrder).where(PurchaseOrder.id == _uuid.UUID(po_id))
        )).scalar_one()
        row.status = "issued"
        await db.commit()

    resp = await admin_client.post(GR_URL, json=_gr_payload(po_id, line_items=[
        {"description": "Hydraulic Pump", "qty_ordered": "2", "qty_received": "2",
         "unit": "EA", "unit_price": "350.00", "condition": "good"},
        {"description": "30% Discount", "qty_ordered": "1", "qty_received": "1",
         "unit": "EA", "unit_price": "-100.00", "condition": "good"},
    ]))
    assert resp.status_code == 201, resp.text
    lines = resp.json()["line_items"]
    assert float(lines[1]["unit_price"]) == -100.00
    assert float(lines[1]["line_total"]) == -100.00


@pytest.mark.asyncio
async def test_create_gr_requires_issued_po(admin_client):
    v = await admin_client.post(VENDOR_URL, json={
        "code": "VND-GR-DRAFT-01", "name": "D", "category": "C",
        "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    po = await admin_client.post(PO_URL, json={
        "title": "Draft PO", "type": 3, "vendor_id": v.json()["id"],
        "currency": "CAD", "tax_rate": "0", "line_items": [_PO_LINE],
    })
    resp = await admin_client.post(GR_URL, json=_gr_payload(po.json()["id"]))
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_gr(admin_client):
    po, _ = await _make_issued_po(admin_client, "VND-GR-GET-01")
    gr = await _create_gr(admin_client, po["id"])
    resp = await admin_client.get(f"{GR_URL}/{gr['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == gr["id"]


@pytest.mark.asyncio
async def test_list_grs(admin_client):
    po, _ = await _make_issued_po(admin_client, "VND-GR-LIST-01")
    await _create_gr(admin_client, po["id"])
    resp = await admin_client.get(GR_URL)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# ── Physical GR workflow ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_physical_gr_full_flow(admin_client):
    po, _ = await _make_issued_po(admin_client, "VND-GR-FLOW-01")
    gr = await _create_gr(admin_client, po["id"])
    gid = gr["id"]

    # acknowledge → collection_pending
    r = await admin_client.post(f"{GR_URL}/{gid}/action", json={
        "action": "acknowledge", "acknowledged_by": "John Doe"
    })
    assert r.json()["status"] == "collection_pending"
    assert r.json()["acknowledged_by"] == "John Doe"

    # collect → collected
    r = await admin_client.post(f"{GR_URL}/{gid}/action", json={
        "action": "collect", "collected_by": "Jane Doe",
        "collection_notes": "Picked up from warehouse"
    })
    assert r.json()["status"] == "collected"
    assert r.json()["collected_by"] == "Jane Doe"

    # confirm → confirmed
    r = await admin_client.post(f"{GR_URL}/{gid}/action", json={"action": "confirm"})
    assert r.json()["status"] == "confirmed"


@pytest.mark.asyncio
async def test_gr_discrepancy(admin_client):
    po, _ = await _make_issued_po(admin_client, "VND-GR-DISC-01")
    gr = await _create_gr(admin_client, po["id"])
    gid = gr["id"]
    await admin_client.post(f"{GR_URL}/{gid}/action", json={"action": "acknowledge"})
    await admin_client.post(f"{GR_URL}/{gid}/action", json={"action": "collect"})
    r = await admin_client.post(f"{GR_URL}/{gid}/action", json={"action": "discrepancy"})
    assert r.json()["status"] == "discrepancy"


@pytest.mark.asyncio
async def test_gr_cancel(admin_client):
    po, _ = await _make_issued_po(admin_client, "VND-GR-CANCEL-01")
    gr = await _create_gr(admin_client, po["id"])
    r = await admin_client.post(f"{GR_URL}/{gr['id']}/action", json={"action": "cancel"})
    assert r.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_service_gr_flow(admin_client):
    """Type 4 (Service) GR → service flow."""
    v = await admin_client.post(VENDOR_URL, json={
        "code": "VND-GR-SVC-01", "name": "Svc Vendor", "category": "Services",
        "contact_name": "Bob", "contact_email": "bob@svc.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    po = await admin_client.post(PO_URL, json={
        "title": "Service PO", "type": 4, "vendor_id": v.json()["id"],
        "currency": "CAD", "tax_rate": "0", "line_items": [_PO_LINE],
    })
    po_id = po.json()["id"]
    for act in ["submit", "approve", "approve", "issue"]:
        await admin_client.post(f"{PO_URL}/{po_id}/action", json={"action": act})

    gr = await _create_gr(admin_client, po_id)
    assert gr["gr_type"] == "service"

    # acknowledge → collection_pending (service confirm step)
    r = await admin_client.post(f"{GR_URL}/{gr['id']}/action", json={"action": "acknowledge"})
    assert r.json()["status"] == "collection_pending"

    # confirm → confirmed
    r = await admin_client.post(f"{GR_URL}/{gr['id']}/action", json={"action": "confirm"})
    assert r.json()["status"] == "confirmed"
