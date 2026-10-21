"""EPMS → finance AP invoice sync tests."""
import uuid
import pytest


@pytest.mark.asyncio
async def test_upsert_ap_invoice_fail_open(monkeypatch):
    """Client returns None (not raise) when finance is unreachable."""
    from app.services import finance_client

    class _BoomClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            import httpx
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(finance_client.httpx, "AsyncClient", _BoomClient)
    out = await finance_client.upsert_ap_invoice(
        payload={"source": "epms", "source_invoice_id": str(uuid.uuid4()),
                 "invoice_date": "2026-06-17", "status": "draft"},
        bearer_token="t",
    )
    assert out is None


from datetime import date


async def _make_vendor(client, code):
    r = await client.post("/api/v1/vendors", json={
        "code": code, "name": "Sync Vendor", "category": "Parts",
        "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD"})
    r.raise_for_status()
    return r.json()


def _inv_payload(vendor_id, **over):
    p = {"vendor_id": vendor_id, "vendor_invoice_number": "V-SYNC-1",
         "amount": "1000.00", "tax_amount": "130.00", "currency": "CAD",
         "invoice_date": "2026-06-17", "due_date": "2026-07-17",
         "line_items": [{"description": "x", "quantity": "1",
                         "unit_price": "1000.00", "line_total": "1000.00"}]}
    p.update(over)
    return p


@pytest.mark.asyncio
async def test_sync_builds_payload_and_maps_status(admin_client, monkeypatch):
    from app.services import finance_sync
    from app.crud import invoice as invoice_crud

    captured = {}
    async def _fake_upsert(*, payload, bearer_token):
        captured["payload"] = payload
        captured["token"] = bearer_token
        return {"ok": True}
    monkeypatch.setattr(finance_sync.finance_client, "upsert_ap_invoice", _fake_upsert)

    v = await _make_vendor(admin_client, "VND-SYNC-01")
    inv = (await admin_client.post("/api/v1/invoices", json=_inv_payload(v["id"]))).json()

    import app.db.session as sm
    async with sm.AsyncSessionLocal() as db:
        orm = await invoice_crud.get_by_id(db, uuid.UUID(inv["id"]))
        await finance_sync.sync_ap_invoice(db, orm, "tok-123")

    p = captured["payload"]
    assert captured["token"] == "tok-123"
    assert p["source"] == "epms"
    assert p["source_invoice_id"] == inv["id"]
    assert p["source_ref"] == inv["internal_ref"]
    assert p["status"] == "draft"
    assert p["source_status"] == "unmatched"
    assert p["amount"] == "1000.00" and p["total_amount"] == "1130.00"
    assert p["invoice_date"] == "2026-06-17"

    captured.clear()
    async with sm.AsyncSessionLocal() as db:
        orm = await invoice_crud.get_by_id(db, uuid.UUID(inv["id"]))
        await finance_sync.sync_ap_invoice(db, orm, "tok", void=True)
    assert captured["payload"]["status"] == "void"


def test_ap_status_mapping():
    from app.services.finance_sync import _ap_status
    assert _ap_status("unmatched", False) == "draft"
    assert _ap_status("exception", False) == "draft"
    assert _ap_status("matched", False) == "posted"
    assert _ap_status("approved", False) == "posted"
    assert _ap_status("paid", False) == "paid"
    assert _ap_status("matched", True) == "void"


@pytest.mark.asyncio
async def test_upload_invoice_triggers_draft_sync(admin_client, monkeypatch):
    """Uploading an invoice upserts a draft AP invoice to finance."""
    import app.api.v1.invoices as invoices_api
    calls = []
    async def _spy(db, invoice, bearer_token, *, void=False):
        calls.append({"status": invoice.status, "void": void, "id": str(invoice.id)})
    monkeypatch.setattr(invoices_api.finance_sync, "sync_ap_invoice", _spy)

    v = await _make_vendor(admin_client, "VND-SYNC-UP-01")
    r = await admin_client.post("/api/v1/invoices", json=_inv_payload(v["id"]))
    assert r.status_code == 201
    assert len(calls) == 1
    assert calls[0]["status"] == "unmatched" and calls[0]["void"] is False


@pytest.mark.asyncio
async def test_delete_invoice_triggers_void_sync(admin_client, monkeypatch):
    import app.api.v1.invoices as invoices_api
    calls = []
    async def _spy(db, invoice, bearer_token, *, void=False):
        calls.append({"void": void})
    monkeypatch.setattr(invoices_api.finance_sync, "sync_ap_invoice", _spy)

    v = await _make_vendor(admin_client, "VND-SYNC-DEL-01")
    inv = (await admin_client.post("/api/v1/invoices", json=_inv_payload(v["id"]))).json()
    r = await admin_client.delete(f"/api/v1/invoices/{inv['id']}")
    assert r.status_code == 204
    assert any(c["void"] for c in calls)
