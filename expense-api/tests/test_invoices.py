"""PRD-OA §2 — expense invoice creation, dedup (409), status lifecycle."""
import uuid
import pytest


_VENDOR_ID = str(uuid.uuid4())   # shared across dedup tests in this module


def _invoice_payload(**overrides) -> dict:
    base = {
        "file_name": "invoice.pdf",
        "file_mime_type": "application/pdf",
        "file_size_bytes": 102400,
        "invoice_number": f"INV-{uuid.uuid4().hex[:8]}",
        "vendor_id": _VENDOR_ID,
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
async def test_create_invoice_minimal(admin_client):
    """POST with no vendor_id or invoice_number is allowed (no dedup check performed)."""
    resp = await admin_client.post(
        "/api/v1/invoices",
        json={
            "file_name": "receipt.jpg",
            "file_mime_type": "image/jpeg",
            "file_size_bytes": 51200,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "reviewed"


@pytest.mark.asyncio
async def test_create_invoice_with_lines(admin_client):
    resp = await admin_client.post("/api/v1/invoices", json=_invoice_payload())
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "reviewed"
    assert len(data["lines"]) == 1
    assert float(data["total_amount"]) == pytest.approx(565.00)


@pytest.mark.asyncio
async def test_get_invoice(admin_client):
    create = await admin_client.post("/api/v1/invoices", json=_invoice_payload())
    inv_id = create.json()["id"]
    resp = await admin_client.get(f"/api/v1/invoices/{inv_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == inv_id


@pytest.mark.asyncio
async def test_get_invoice_not_found(admin_client):
    resp = await admin_client.get(f"/api/v1/invoices/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invoice_dedup_same_vendor_and_number(admin_client):
    """PRD-OA §2 — duplicate (vendor_id, invoice_number) → HTTP 409."""
    inv_num = f"DUP-{uuid.uuid4().hex[:6]}"
    payload = _invoice_payload(invoice_number=inv_num)
    r1 = await admin_client.post("/api/v1/invoices", json=payload)
    assert r1.status_code == 201

    r2 = await admin_client.post("/api/v1/invoices", json=payload)
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    assert "already recorded" in detail["message"].lower() or "duplicate" in str(detail).lower()


@pytest.mark.asyncio
async def test_invoice_dedup_different_vendor_allowed(admin_client):
    """Same invoice_number but different vendor_id is NOT a duplicate."""
    inv_num = f"SAME-NUM-{uuid.uuid4().hex[:6]}"
    r1 = await admin_client.post(
        "/api/v1/invoices",
        json=_invoice_payload(invoice_number=inv_num, vendor_id=str(uuid.uuid4())),
    )
    assert r1.status_code == 201

    r2 = await admin_client.post(
        "/api/v1/invoices",
        json=_invoice_payload(invoice_number=inv_num, vendor_id=str(uuid.uuid4())),
    )
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_invoice_dedup_no_vendor_no_number_skipped(admin_client):
    """Both fields absent → dedup check is skipped; two 201s are valid."""
    payload = {"file_name": "scan.pdf", "file_mime_type": "application/pdf", "file_size_bytes": 1024}
    r1 = await admin_client.post("/api/v1/invoices", json=payload)
    r2 = await admin_client.post("/api/v1/invoices", json=payload)
    assert r1.status_code == 201
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_check_duplicate_flags_existing(admin_client):
    """check-duplicate returns the existing record once an invoice is recorded."""
    vendor_id = str(uuid.uuid4())
    inv_num = f"CHK-{uuid.uuid4().hex[:6]}"
    create = await admin_client.post(
        "/api/v1/invoices",
        json=_invoice_payload(invoice_number=inv_num, vendor_id=vendor_id),
    )
    assert create.status_code == 201

    resp = await admin_client.get(
        "/api/v1/invoices/check-duplicate",
        params={"vendor_id": vendor_id, "invoice_number": inv_num},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["duplicate"] is not None
    assert body["duplicate"]["source"] == "oa"


@pytest.mark.asyncio
async def test_check_duplicate_clear_when_none(admin_client):
    """check-duplicate returns null when no matching invoice exists."""
    resp = await admin_client.get(
        "/api/v1/invoices/check-duplicate",
        params={"vendor_id": str(uuid.uuid4()), "invoice_number": f"NEW-{uuid.uuid4().hex[:6]}"},
    )
    assert resp.status_code == 200
    assert resp.json()["duplicate"] is None


@pytest.mark.asyncio
async def test_invoice_unauthenticated(client):
    resp = await client.post("/api/v1/invoices", json=_invoice_payload())
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invoice_line_unit_roundtrip(admin_client):
    payload = _invoice_payload()
    payload["lines"][0]["unit"] = "kg"
    resp = await admin_client.post("/api/v1/invoices", json=payload)
    assert resp.status_code == 201
    assert resp.json()["lines"][0]["unit"] == "kg"


@pytest.mark.asyncio
async def test_invoice_line_unit_optional(admin_client):
    """Lines without a unit still serialize (unit is nullable)."""
    resp = await admin_client.post("/api/v1/invoices", json=_invoice_payload())
    assert resp.status_code == 201
    assert resp.json()["lines"][0]["unit"] is None
