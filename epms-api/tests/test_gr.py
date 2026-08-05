"""Goods Receipt endpoint tests."""
import uuid as _uuid

import pytest
from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

GR_URL = "/api/v1/gr"
PO_URL = "/api/v1/po"
VENDOR_URL = "/api/v1/vendors"

_PO_LINE = {"description": "Hydraulic Pump", "qty": "2", "unit": "EA", "unit_price": "350.00"}


@pytest.fixture(autouse=True)
def _quiet_notification_email(monkeypatch):
    """Silence outbound notification email for every GR test.

    Without this, fire_and_forget_notify's background session retries SMTP with
    2s/4s backoff while the next test's engine fixture runs DROP ... CASCADE —
    a reliable deadlock recipe on the shared test DB."""
    async def _noop(to, subject, html, **kwargs):
        return None

    monkeypatch.setattr("app.services.email.send_email", _noop)


async def _make_issued_po(client, engine, vendor_code, *, po_type=3, with_pr=True):
    """Create a vendor + issued PO for GR tests.

    The PO is flipped to ``issued`` directly in the DB (the approve path needs a
    live approval-api). ``with_pr=True`` also inserts a minimal approved PR owned
    by a fresh requester and links the PO to it — the GR requester flow
    (acknowledge/collect/confirm) only exists when the PO has a PR requester.
    """
    from app.crud import user as user_crud
    from app.models.po import PurchaseOrder
    from app.models.pr import PurchaseRequest
    from app.schemas.auth import RegisterRequest

    v = await client.post(VENDOR_URL, json={
        "code": vendor_code, "name": "GR Vendor", "category": "Parts",
        "contact_name": "Alice", "contact_email": "alice@vendor.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    v.raise_for_status()
    vid = v.json()["id"]

    po = await client.post(PO_URL, json={
        "title": "GR Test PO", "type": po_type, "vendor_id": vid,
        "currency": "CAD", "tax_rate": "0", "line_items": [_PO_LINE],
    })
    po.raise_for_status()
    po_id = po.json()["id"]

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(
            _select(PurchaseOrder).where(PurchaseOrder.id == _uuid.UUID(po_id))
        )).scalar_one()
        row.status = "issued"
        if with_pr:
            requester = await user_crud.create(db, RegisterRequest(
                email=f"gr-req-{_uuid.uuid4().hex[:8]}@example.com",
                password="TestPass1!", full_name="GR Requester", role="requester",
            ))
            pr = PurchaseRequest(
                number=f"PR-GRT-{_uuid.uuid4().hex[:10]}", title="GR test PR",
                type=po_type, status="approved", currency="CAD",
                created_by=requester.id,
            )
            db.add(pr)
            await db.flush()
            row.pr_id = pr.id
            row.pr_number = pr.number
        await db.commit()

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
async def test_create_gr(admin_client, test_engine):
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-GR-CREATE-01")
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
async def test_get_gr(admin_client, test_engine):
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-GR-GET-01")
    gr = await _create_gr(admin_client, po["id"])
    resp = await admin_client.get(f"{GR_URL}/{gr['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == gr["id"]


@pytest.mark.asyncio
async def test_list_grs(admin_client, test_engine):
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-GR-LIST-01")
    await _create_gr(admin_client, po["id"])
    resp = await admin_client.get(GR_URL)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# ── Physical GR workflow ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_physical_gr_full_flow(admin_client, test_engine):
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-GR-FLOW-01")
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
async def test_gr_discrepancy(admin_client, test_engine):
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-GR-DISC-01")
    gr = await _create_gr(admin_client, po["id"])
    gid = gr["id"]
    await admin_client.post(f"{GR_URL}/{gid}/action", json={"action": "acknowledge"})
    await admin_client.post(f"{GR_URL}/{gid}/action", json={"action": "collect"})
    r = await admin_client.post(f"{GR_URL}/{gid}/action", json={"action": "discrepancy"})
    assert r.json()["status"] == "discrepancy"


@pytest.mark.asyncio
async def test_gr_cancel(admin_client, test_engine):
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-GR-CANCEL-01")
    gr = await _create_gr(admin_client, po["id"])
    r = await admin_client.post(f"{GR_URL}/{gr['id']}/action", json={"action": "cancel"})
    assert r.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_service_gr_flow(admin_client, test_engine):
    """Type 4 (Service) GR → service flow."""
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-GR-SVC-01", po_type=4)
    po_id = po["id"]

    gr = await _create_gr(admin_client, po_id)
    assert gr["gr_type"] == "service"

    # acknowledge → collection_pending (service confirm step)
    r = await admin_client.post(f"{GR_URL}/{gr['id']}/action", json={"action": "acknowledge"})
    assert r.json()["status"] == "collection_pending"

    # confirm → confirmed
    r = await admin_client.post(f"{GR_URL}/{gr['id']}/action", json={"action": "confirm"})
    assert r.json()["status"] == "confirmed"


# ── GR on a PO with no linked PR (imported POs) ────────────────────────────────
# There is no requester to acknowledge/collect/confirm, so the requester steps
# are skipped automatically and system admins get an alert email instead.
# Regression: 2026-08-05 — the ack task fell back to a role-wide broadcast and
# mailed every active requester in the company (59 people, twice).

@pytest.fixture
def captured_admin_alerts(monkeypatch):
    alerts: list[tuple] = []

    def _fake_alert(subject, body):
        alerts.append((subject, body))

    monkeypatch.setattr(
        "app.services.notification.fire_and_forget_admin_alert", _fake_alert
    )
    return alerts


async def _open_requester_tasks(engine, gr_id):
    from app.models.task import Task
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        return (await db.execute(_select(Task).where(
            Task.document_type == "gr",
            Task.document_id == _uuid.UUID(gr_id),
            Task.assigned_role == "requester",
            Task.is_completed.is_(False),
        ))).scalars().all()


@pytest.mark.asyncio
async def test_create_gr_no_pr_physical_auto_completes(admin_client, test_engine, captured_admin_alerts):
    from app.models.po import PoLineItem
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-GR-NOPR-01", with_pr=False)
    gr = await _create_gr(admin_client, po["id"], line_items=[{
        "description": "Hydraulic Pump", "qty_ordered": "2", "qty_received": "2",
        "unit": "EA", "unit_price": "350.00", "condition": "good",
        "po_line_id": po["line_items"][0]["id"],
    }])

    assert gr["status"] == "collected"                 # requester steps skipped
    assert gr["acknowledged_by"].startswith("auto")
    assert await _open_requester_tasks(test_engine, gr["id"]) == []
    assert len(captured_admin_alerts) == 1
    subject, body = captured_admin_alerts[0]
    assert gr["number"] in subject
    assert po["number"] in subject or po["number"] in body

    # received_qty must still be written back to the PO line
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        line = (await db.execute(_select(PoLineItem).where(
            PoLineItem.po_id == _uuid.UUID(po["id"])
        ))).scalar_one()
        assert float(line.received_qty) == 2.0


@pytest.mark.asyncio
async def test_create_gr_no_pr_service_auto_confirms(admin_client, test_engine, captured_admin_alerts):
    po, _ = await _make_issued_po(
        admin_client, test_engine, "VND-GR-NOPR-02", po_type=4, with_pr=False,
    )
    gr = await _create_gr(admin_client, po["id"])

    assert gr["gr_type"] == "service"
    assert gr["status"] == "confirmed"                 # ack + confirm collapsed
    assert await _open_requester_tasks(test_engine, gr["id"]) == []
    assert len(captured_admin_alerts) == 1


@pytest.mark.asyncio
async def test_gr_with_pr_still_requires_ack(admin_client, test_engine, captured_admin_alerts):
    """PR-linked POs keep the normal requester flow — and no admin alert."""
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-GR-NOPR-03")
    gr = await _create_gr(admin_client, po["id"])
    assert gr["status"] == "pending_ack"
    tasks = await _open_requester_tasks(test_engine, gr["id"])
    assert len(tasks) == 1
    assert tasks[0].assigned_user_id is not None       # pinned, not a broadcast
    assert captured_admin_alerts == []
