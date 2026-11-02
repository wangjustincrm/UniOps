"""Procurement batch 12b — ISS-0189: the exception reason quotes the pre-tax
figures the variance is measured on (INV-3WM-001)."""
import uuid as _uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_invoice_allocations import (
    INV_URL, _inv_payload, _make_issued_po, _make_vendor,
)


@pytest.fixture(autouse=True)
def _quiet_notification_email(monkeypatch):
    async def _noop(to, subject, html, **kwargs):
        return None

    monkeypatch.setattr("app.services.email.send_email", _noop)


async def _exception_invoice(client, code):
    """Invoice pre-tax 44.00 + tax 5.72 = 49.72, matched against a PO line of
    40.00 pre-tax: variance +4.00 (10%), outside tolerance."""
    v = await _make_vendor(client, code)
    po = await _make_issued_po(client, v["id"],
        lines=[{"description": "L", "qty": "1", "unit": "EA", "unit_price": "40.00"}])
    inv = (await client.post(INV_URL, json=_inv_payload(
        v["id"], vendor_invoice_number=f"INV-{code}", amount="44.00", tax_amount="5.72",
        line_items=[{"description": "L", "quantity": "1",
                     "unit_price": "44.00", "line_total": "44.00"}]))).json()
    r = await client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [{"invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
                         "po_line_id": po["line_items"][0]["id"],
                         "allocated_amount": "44.00", "allocated_tax": "5.72"}],
    })
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_exception_reason_compares_pre_tax_figures(admin_client):
    """ISS-0189 #3: the reason used to quote the tax-inclusive invoice total
    (49.72) beside the pre-tax PO reference (40.00); the variance is pre-tax
    (INV-3WM-001), so both figures must be."""
    body = await _exception_invoice(admin_client, "VND-B12B-EXC-01")
    assert body["status"] == "exception"
    reason = body["exception_reason"]
    assert "invoice pre-tax 44.00 vs PO pre-tax 40.00" in reason, reason
    assert "49.72" not in reason


@pytest.mark.asyncio
async def test_reviewed_exception_reason_compares_pre_tax_figures(admin_client, test_engine):
    """Same wording on the Match Review → approve path."""
    from app.models.invoice import Invoice

    body = await _exception_invoice(admin_client, "VND-B12B-EXC-02")
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        inv = (await db.execute(
            select(Invoice).where(Invoice.id == _uuid.UUID(body["id"]))
        )).scalar_one()
        inv.status = "match_review"
        inv.exception_reason = None
        await db.commit()

    r = await admin_client.post(f"{INV_URL}/{body['id']}/match-review",
                                json={"action": "approve", "note": "checked"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "exception"
    reason = r.json()["exception_reason"]
    assert "invoice pre-tax 44.00 vs PO pre-tax 40.00" in reason, reason
    assert "49.72" not in reason
