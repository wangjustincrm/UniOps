"""OA → finance AP invoice sync tests."""
import uuid
import pytest


@pytest.mark.asyncio
async def test_upsert_ap_invoice_fail_open(monkeypatch):
    from app.services import finance_client

    class _BoomClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            import httpx
            raise httpx.ConnectError("boom")

    # conftest blanks finance_api_url (disables sync); re-set it so this test
    # exercises the real HTTP fail-open path past the "not configured" guard.
    monkeypatch.setattr(finance_client.settings, "finance_api_url", "http://finance.test")
    monkeypatch.setattr(finance_client.httpx, "AsyncClient", _BoomClient)
    out = await finance_client.upsert_ap_invoice(
        payload={"source": "oa", "source_invoice_id": str(uuid.uuid4()),
                 "invoice_date": "2026-06-17", "status": "draft"},
        bearer_token="t",
    )
    assert out is None


@pytest.mark.asyncio
async def test_upsert_ap_invoice_skips_when_url_blank(monkeypatch):
    """No finance_api_url configured → AP sync is disabled, no HTTP attempted."""
    from app.services import finance_client

    monkeypatch.setattr(finance_client.settings, "finance_api_url", "")

    def _boom(*a, **k):
        raise AssertionError("httpx.AsyncClient must not be constructed when sync disabled")

    monkeypatch.setattr(finance_client.httpx, "AsyncClient", _boom)
    out = await finance_client.upsert_ap_invoice(
        payload={"source": "oa", "source_invoice_id": str(uuid.uuid4())},
        bearer_token="t",
    )
    assert out is None


def test_ap_status_mapping():
    from app.services.finance_sync import _ap_status
    assert _ap_status("reviewed", False) == "draft"
    assert _ap_status("uploaded", False) == "draft"
    assert _ap_status("used", False) == "posted"
    assert _ap_status("used", True) == "void"


@pytest.mark.asyncio
async def test_sync_builds_oa_payload(monkeypatch):
    """sync_ap_invoice builds a JSON-ready OA payload; invoice_date falls back to created_at."""
    import datetime as _dt
    from app.services import finance_sync

    captured = {}
    async def _fake_upsert(*, payload, bearer_token):
        captured["payload"] = payload
        return {"ok": True}
    monkeypatch.setattr(finance_sync.finance_client, "upsert_ap_invoice", _fake_upsert)

    class _Inv:
        id = uuid.uuid4()
        invoice_number = "INV-OA-1"
        vendor_id = uuid.uuid4()
        vendor_name = "ULINE"
        invoice_date = None
        due_date = None
        currency = "CAD"
        subtotal = "1000.00"
        tax_amount = "130.00"
        total_amount = "1130.00"
        status = "used"
        created_at = _dt.datetime(2026, 6, 20, 12, 0, tzinfo=_dt.timezone.utc)

    await finance_sync.sync_ap_invoice(db=None, invoice=_Inv(), bearer_token="t")
    p = captured["payload"]
    assert p["source"] == "oa"
    assert p["source_invoice_id"] == str(_Inv.id)
    assert p["status"] == "posted"
    assert p["amount"] == "1000.00" and p["total_amount"] == "1130.00"
    assert p["invoice_date"] == "2026-06-20"
    assert p["due_date"] is None
    assert p["tax_lines"] == []


# ── Endpoint wiring tests ───────────────────────────────────────────────────────

def _invoice_body(**overrides) -> dict:
    base = {
        "file_name": "invoice.pdf",
        "file_mime_type": "application/pdf",
        "file_size_bytes": 102400,
        "invoice_number": f"INV-{uuid.uuid4().hex[:8]}",
        "vendor_id": str(uuid.uuid4()),
        "vendor_name": "Test Vendor",
        "currency": "CAD",
        "subtotal": "500.00",
        "tax_amount": "65.00",
        "total_amount": "565.00",
        "lines": [
            {
                "line_number": 1,
                "description": "Consulting services",
                "quantity": "1",
                "unit_price": "500.00",
                "amount": "500.00",
                "tax_amount": "65.00",
            }
        ],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_create_invoice_triggers_draft_sync(admin_client, monkeypatch):
    import app.api.v1.invoices as invoices_api
    calls = []

    async def _spy(db, invoice, bearer_token, *, void=False):
        calls.append({"status": invoice.status, "void": void})

    monkeypatch.setattr(invoices_api.finance_sync, "sync_ap_invoice", _spy)

    r = await admin_client.post("/api/v1/invoices", json=_invoice_body())
    assert r.status_code == 201, r.text
    assert len(calls) == 1
    assert calls[0]["status"] == "reviewed"
    assert calls[0]["void"] is False


@pytest.mark.asyncio
async def test_update_invoice_triggers_draft_sync(admin_client, monkeypatch):
    import app.api.v1.invoices as invoices_api
    calls = []

    async def _spy(db, invoice, bearer_token, *, void=False):
        calls.append({"status": invoice.status, "void": void})

    # Create first (don't count this towards the assertion)
    create = await admin_client.post("/api/v1/invoices", json=_invoice_body())
    assert create.status_code == 201, create.text
    inv_id = create.json()["id"]

    monkeypatch.setattr(invoices_api.finance_sync, "sync_ap_invoice", _spy)
    r = await admin_client.patch(
        f"/api/v1/invoices/{inv_id}", json={"vendor_name": "Renamed Vendor"}
    )
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    assert calls[0]["status"] == "reviewed"
    assert calls[0]["void"] is False


@pytest.mark.asyncio
async def test_pa_create_marks_invoice_posted(admin_client, monkeypatch):
    import app.api.v1.pa as pa_api
    calls = []

    async def _spy(db, invoice, bearer_token, *, void=False):
        calls.append(invoice.status)

    monkeypatch.setattr(pa_api.finance_sync, "sync_ap_invoice", _spy)

    create = await admin_client.post("/api/v1/invoices", json=_invoice_body())
    assert create.status_code == 201, create.text
    inv_id = create.json()["id"]

    r = await admin_client.post(
        "/api/v1/pa/direct",
        json={
            "invoice_id": inv_id,
            "vendor_name": "Test Vendor",
            "payment_amount": "565.00",
            "currency": "CAD",
        },
    )
    assert r.status_code == 201, r.text
    # sync called after invoice.status flipped to "used" → maps to posted
    assert "used" in calls
