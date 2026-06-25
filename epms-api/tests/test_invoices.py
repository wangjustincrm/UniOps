"""Invoice endpoint tests."""
import pytest

INV_URL = "/api/v1/invoices"
VENDOR_URL = "/api/v1/vendors"
PO_URL = "/api/v1/po"
GR_URL = "/api/v1/gr"

_PO_LINE = {"description": "Widget", "qty": "10", "unit": "EA", "unit_price": "100.00"}


async def _make_vendor(client, code):
    r = await client.post(VENDOR_URL, json={
        "code": code, "name": "Inv Vendor", "category": "Parts",
        "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _make_issued_po(client, vendor_id, po_type=2):
    po = await client.post(PO_URL, json={
        "title": "Inv Test PO", "type": po_type, "vendor_id": vendor_id,
        "currency": "CAD", "tax_rate": "0.13", "line_items": [_PO_LINE],
    })
    po.raise_for_status()
    po_id = po.json()["id"]
    for act in ["submit", "approve", "approve", "issue"]:
        (await client.post(f"{PO_URL}/{po_id}/action", json={"action": act})).raise_for_status()
    return po.json()


def _inv_payload(vendor_id, **overrides):
    base = {
        "vendor_id": vendor_id,
        "vendor_invoice_number": "INV-VENDOR-001",
        "amount": "1000.00",
        "tax_amount": "130.00",
        "currency": "CAD",
        "invoice_date": "2026-03-20",
        "due_date": "2026-04-19",
    }
    base.update(overrides)
    return base


async def _create_inv(client, vendor_id, **overrides):
    r = await client.post(INV_URL, json=_inv_payload(vendor_id, **overrides))
    assert r.status_code == 201, r.text
    return r.json()


# ── Basic CRUD ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_invoice(admin_client):
    v = await _make_vendor(admin_client, "VND-INV-CREATE-01")
    inv = await _create_inv(admin_client, v["id"])
    assert inv["internal_ref"].startswith("INV-")
    assert inv["status"] == "unmatched"
    assert float(inv["total_amount"]) == 1130.00


@pytest.mark.asyncio
async def test_list_invoices(admin_client):
    v = await _make_vendor(admin_client, "VND-INV-LIST-01")
    await _create_inv(admin_client, v["id"])
    r = await admin_client.get(INV_URL)
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_get_invoice(admin_client):
    v = await _make_vendor(admin_client, "VND-INV-GET-01")
    inv = await _create_inv(admin_client, v["id"])
    r = await admin_client.get(f"{INV_URL}/{inv['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == inv["id"]


# ── 3-way match ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_match_invoice_zero_variance(admin_client):
    """Invoice total == PO total → status becomes 'matched'."""
    v = await _make_vendor(admin_client, "VND-INV-MATCH-01")
    po = await _make_issued_po(admin_client, v["id"])
    po_total = float(po["total"])  # 10 * 100 * 1.13 = 1130

    inv = await _create_inv(admin_client, v["id"],
                             amount=str(po["subtotal"]),
                             tax_amount=str(po["tax_amount"]))
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": po["id"]})
    assert r.status_code == 200
    assert r.json()["status"] == "matched"
    assert float(r.json()["variance"]) == 0.0


@pytest.mark.asyncio
async def test_match_invoice_variance_creates_exception(admin_client):
    """Invoice total != PO total → status becomes 'exception'."""
    v = await _make_vendor(admin_client, "VND-INV-EXCEPT-01")
    po = await _make_issued_po(admin_client, v["id"])

    # Invoice for a different amount
    inv = await _create_inv(admin_client, v["id"],
                             amount="950.00", tax_amount="123.50")
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": po["id"]})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "exception"
    assert data["variance"] is not None
    assert float(data["variance"]) != 0


@pytest.mark.asyncio
async def test_resolve_exception_accepted(admin_client):
    v = await _make_vendor(admin_client, "VND-INV-RESOLVE-01")
    po = await _make_issued_po(admin_client, v["id"])
    inv = await _create_inv(admin_client, v["id"], amount="800.00", tax_amount="104.00")
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": po["id"]})

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/exception", json={
        "resolution": "accepted", "note": "Approved by manager"
    })
    assert r.status_code == 200
    assert r.json()["status"] == "matched"
    assert r.json()["exception_resolution"] == "accepted"


@pytest.mark.asyncio
async def test_resolve_exception_credit_note(admin_client):
    v = await _make_vendor(admin_client, "VND-INV-CREDIT-01")
    po = await _make_issued_po(admin_client, v["id"])
    inv = await _create_inv(admin_client, v["id"], amount="800.00", tax_amount="104.00")
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": po["id"]})

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/exception", json={
        "resolution": "credit_note_requested"
    })
    assert r.status_code == 200
    # credit note → stays as exception (not auto-matched)
    assert r.json()["exception_resolution"] == "credit_note_requested"


@pytest.mark.asyncio
async def test_cannot_rematch_matched_invoice(admin_client):
    v = await _make_vendor(admin_client, "VND-INV-REMATCH-01")
    po = await _make_issued_po(admin_client, v["id"])
    inv = await _create_inv(admin_client, v["id"],
                             amount=str(po["subtotal"]), tax_amount=str(po["tax_amount"]))
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": po["id"]})
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": po["id"]})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_invoice_unauthenticated(client):
    r = await client.post(INV_URL, json=_inv_payload("00000000-0000-0000-0000-000000000001"))
    assert r.status_code == 403
