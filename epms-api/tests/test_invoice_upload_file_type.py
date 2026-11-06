"""INV-001 on the server: an invoice file is a PDF, JPG, JPEG or PNG (ISS-0093).

The upload form creates the invoice first and stores its file afterwards, so a
file refused at the second step (or never checked) left an invoice behind — a
.zip became INV-2026-0336. POST /invoices now refuses the record itself.
"""
import pytest

from tests.test_invoice_assign import INV_URL, _make_vendor


def _body(vendor_id, number, file_name):
    return {
        "vendor_id": vendor_id, "vendor_invoice_number": number,
        "amount": "10.00", "currency": "CAD",
        "invoice_date": "2026-10-22", "due_date": "2026-11-21",
        "file_name": file_name, "file_size": "1.0 KB",
    }


@pytest.mark.asyncio
async def test_zip_invoice_is_refused_and_not_created(admin_client):
    v = await _make_vendor(admin_client, "VND-FTYPE-01")
    r = await admin_client.post(INV_URL, json=_body(v["id"], "R2-ZIP-TEST-01", "R2-ZIP-TEST-01.zip"))
    assert r.status_code == 422
    assert "PDF, JPG, JPEG, PNG" in r.text
    listed = (await admin_client.get(INV_URL, params={"search": "R2-ZIP-TEST-01"})).json()
    assert listed["total"] == 0


@pytest.mark.asyncio
async def test_inv001_types_and_manual_entry_are_accepted(admin_client):
    v = await _make_vendor(admin_client, "VND-FTYPE-02")
    for i, name in enumerate(("a.pdf", "b.JPG", "c.jpeg", "d.png", None)):
        r = await admin_client.post(INV_URL, json=_body(v["id"], f"FTYPE-OK-{i}", name))
        assert r.status_code == 201, (name, r.text)
        assert r.json()["file_name"] == name
