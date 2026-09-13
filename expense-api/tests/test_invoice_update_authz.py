"""PATCH /invoices/{id} object-level authorization.

The 2026-08 OA IDOR sweep gated every invoice READ and left this WRITE on
`CurrentUserDep` alone — i.e. on "is logged in". Any employee could rewrite any
invoice's vendor, number, dates and amounts, and the tail of the handler pushes
the result into finance's `ap_invoices` via sync_ap_invoice, so it did not even
stay inside this service.

Same rule as the reads: the uploader, system_admin, or a payment-stage role.
"""
import uuid


async def _invoice(client, **overrides) -> dict:
    body = {
        "file_name": "vendor_invoice.pdf",
        "file_mime_type": "application/pdf",
        "file_size_bytes": 204800,
        "invoice_number": f"INV-{uuid.uuid4().hex[:8]}",
        "vendor_id": str(uuid.uuid4()),
        "vendor_name": "Titan Power Ltd",
        "currency": "CAD",
        "subtotal": "1000.00",
        "tax_amount": "130.00",
        "total_amount": "1130.00",
    }
    body.update(overrides)
    resp = await client.post("/api/v1/invoices", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_stranger_cannot_edit_someone_elses_invoice(requester_client, requester_client_b):
    inv = await _invoice(requester_client)

    resp = await requester_client_b.patch(
        f"/api/v1/invoices/{inv['id']}", json={"total_amount": "99999.00"})

    assert resp.status_code == 403
    assert "Not authorized" in resp.json()["detail"]


async def test_the_amount_is_actually_unchanged_after_a_refused_edit(
    requester_client, requester_client_b,
):
    """Guard against a 403 that still wrote — the handler mutates in place."""
    inv = await _invoice(requester_client)

    await requester_client_b.patch(
        f"/api/v1/invoices/{inv['id']}", json={"total_amount": "99999.00"})

    after = await requester_client.get(f"/api/v1/invoices/{inv['id']}")
    assert after.status_code == 200
    assert after.json()["total_amount"] == inv["total_amount"]


async def test_uploader_can_edit_their_own_invoice(requester_client):
    inv = await _invoice(requester_client)

    resp = await requester_client.patch(
        f"/api/v1/invoices/{inv['id']}", json={"vendor_name": "Titan Power Ltd."})

    assert resp.status_code == 200
    assert resp.json()["vendor_name"] == "Titan Power Ltd."


async def test_admin_can_edit_any_invoice(requester_client, admin_client):
    inv = await _invoice(requester_client)

    resp = await admin_client.patch(
        f"/api/v1/invoices/{inv['id']}", json={"vendor_name": "Corrected Vendor"})

    assert resp.status_code == 200
    assert resp.json()["vendor_name"] == "Corrected Vendor"


async def test_finance_can_edit_any_invoice(requester_client, finance_client):
    """Payment-stage roles keep write access — they correct OCR slips at review."""
    inv = await _invoice(requester_client)

    resp = await finance_client.patch(
        f"/api/v1/invoices/{inv['id']}", json={"tax_amount": "130.50"})

    assert resp.status_code == 200
    assert resp.json()["tax_amount"] == "130.50"
