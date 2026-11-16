"""Procurement batch 15 — invoice matching and create_pa tasks.

ISS-0229: a header-level allocation compares against the PO pre-tax subtotal
using everything billed on the PO, line-level allocations of other invoices
included (INV-3WM-001).
ISS-0129: the exception reason quotes the cumulative figures, not this
invoice alone.
"""
import pytest

from tests.test_invoice_allocations import (
    INV_URL, _inv_payload, _make_issued_po, _make_vendor,
)


@pytest.fixture(autouse=True)
def _quiet_notification_email(monkeypatch):
    async def _noop(to, subject, html, **kwargs):
        return None

    monkeypatch.setattr("app.services.email.send_email", _noop)


async def _invoice(client, vendor_id, number, amount):
    r = await client.post(INV_URL, json=_inv_payload(
        vendor_id, vendor_invoice_number=number, amount=amount, tax_amount="0.00",
        line_items=[{"description": "L", "quantity": "1",
                     "unit_price": amount, "line_total": amount}]))
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _match_line(client, inv, po, amount):
    r = await client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [{"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
                         "po_line_id": po["line_items"][0]["id"],
                         "allocated_amount": amount, "allocated_tax": "0"}],
    })
    assert r.status_code == 200, r.text
    return r.json()


async def _match_header(client, inv, po, amount):
    r = await client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [{"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
                         "po_line_id": None,
                         "allocated_amount": amount, "allocated_tax": "0"}],
    })
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_header_match_counts_line_allocations_of_other_invoices(admin_client):
    """ISS-0229: PO 310 billed in full line by line by invoice A; invoice B of
    40 matched to the PO header must see 350 vs 310 (+12.9%) → Exception, not
    "partially invoiced" against its own 40."""
    v = await _make_vendor(admin_client, "VND-B15-HDR-01")
    po = await _make_issued_po(admin_client, v["id"], lines=[
        {"description": "L", "qty": "1", "unit": "EA", "unit_price": "310.00"}])
    a = await _invoice(admin_client, v["id"], "B15-HDR-A", "310.00")
    assert (await _match_line(admin_client, a, po, "310.00"))["status"] == "matched"

    b = await _invoice(admin_client, v["id"], "B15-HDR-B", "40.00")
    body = await _match_header(admin_client, b, po, "40.00")
    assert body["status"] == "exception", body
    assert "all invoices pre-tax 350.00 vs PO pre-tax 310.00" in body["exception_reason"]

    detail = (await admin_client.get(f"{INV_URL}/{b['id']}")).json()
    assert float(detail["cumulative_variance"]) == pytest.approx(40.0)
    assert a["internal_ref"] in detail["cumulative_other_invoices"]


@pytest.mark.asyncio
async def test_header_match_within_po_still_passes(admin_client):
    """Partial billing still passes: 200 by line + 100 on the header = 300 of
    310 → Matched (INV-3WM-002)."""
    v = await _make_vendor(admin_client, "VND-B15-HDR-02")
    po = await _make_issued_po(admin_client, v["id"], lines=[
        {"description": "L", "qty": "1", "unit": "EA", "unit_price": "310.00"}])
    a = await _invoice(admin_client, v["id"], "B15-HDR2-A", "200.00")
    await _match_line(admin_client, a, po, "200.00")
    b = await _invoice(admin_client, v["id"], "B15-HDR2-B", "100.00")
    assert (await _match_header(admin_client, b, po, "100.00"))["status"] == "matched"


@pytest.mark.asyncio
async def test_exception_reason_uses_cumulative_figures(admin_client):
    """ISS-0129: PO line 20; invoice A 20 matched, invoice B 20 → Exception.
    The reason must read 40.00 vs 20.00, not 20.00 vs 20.00."""
    v = await _make_vendor(admin_client, "VND-B15-RSN-01")
    po = await _make_issued_po(admin_client, v["id"], lines=[
        {"description": "L", "qty": "1", "unit": "EA", "unit_price": "20.00"}])
    a = await _invoice(admin_client, v["id"], "B15-RSN-A", "20.00")
    await _match_line(admin_client, a, po, "20.00")
    b = await _invoice(admin_client, v["id"], "B15-RSN-B", "20.00")
    body = await _match_line(admin_client, b, po, "20.00")
    assert body["status"] == "exception"
    reason = body["exception_reason"]
    assert "all invoices pre-tax 40.00 vs PO pre-tax 20.00" in reason, reason
    assert "20.00 vs PO pre-tax 20.00" not in reason

