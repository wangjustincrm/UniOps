"""Tracker "Procurement batch 1" — payment safety on PAs and invoices.

ISS-0112  one invoice on two live PAs (Edit of a returned PA, any route)
ISS-0125  editing / deleting an invoice a live PA pays
ISS-0120  matching an invoice to another vendor's PO
ISS-0109  PA lines from the invoices' own lines (agreement route, PO header match)
ISS-0129  the variance that decides Exception, on the invoice detail
"""
import uuid as _uuid
from datetime import date as _date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from tests.test_invoice_allocations import (
    INV_URL, _ensure_company_config, _inv_payload, _make_issued_po, _make_vendor,
)
from tests.test_pa_invoice_due_window import _po_with_invoices

PA_URL = "/api/v1/pa"


def _pa_body(po_id, invoice_ids, **over):
    n = len(invoice_ids)
    body = {"po_id": po_id, "title": "Batch1 PA", "pa_type": "regular",
            "subtotal": f"{100 * n}.00", "tax_amount": f"{13 * n}.00", "currency": "CAD",
            "invoice_ids": invoice_ids}
    body.update(over)
    return body


async def _set(test_engine, model, row_id, **values):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(select(model).where(model.id == _uuid.UUID(str(row_id))))).scalar_one()
        for k, v in values.items():
            setattr(row, k, v)
        await db.commit()


async def _agreement_with_invoices(client, test_engine, n=2):
    from tests.test_agreement_invoice_match import _make_active_agreement, _upload_invoice
    from tests.test_agreement_pa import _declare_legacy_settlement
    from tests.test_agreements import seed_vendor_and_user

    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    invs = []
    for _ in range(n):
        inv = await _upload_invoice(client, vendor_id, amount="100.00")
        m = await client.post(f"{INV_URL}/{inv['id']}/match", json={"agreement_id": str(agr.id)})
        assert m.status_code == 200, m.text
        await _declare_legacy_settlement(test_engine, inv["id"])
        invs.append(inv)
    return agr, invs


# ── ISS-0112 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_po_route_second_pa_on_same_invoice_refused(admin_client, test_engine):
    po, invs = await _po_with_invoices(admin_client, test_engine, [_date(2026, 3, 2)] * 2)
    r = await admin_client.post(PA_URL, json=_pa_body(po["id"], [invs[0]]))
    assert r.status_code == 201, r.text
    first = r.json()["pa_number"]

    r = await admin_client.post(PA_URL, json=_pa_body(po["id"], [invs[0]]))
    assert r.status_code == 409, r.text
    assert first in r.json()["detail"] and "paid twice" in r.json()["detail"]

    # PATCH of another draft cannot pick it up either; its own invoice is fine.
    r = await admin_client.post(PA_URL, json=_pa_body(po["id"], [invs[1]]))
    assert r.status_code == 201, r.text
    second = r.json()["id"]
    r = await admin_client.patch(f"{PA_URL}/{second}", json={"invoice_ids": invs})
    assert r.status_code == 409, r.text
    r = await admin_client.patch(f"{PA_URL}/{second}", json={"invoice_ids": [invs[1]], "title": "t"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_agreement_pa_edit_cannot_reclaim_invoice(admin_client, test_engine):
    agr, invs = await _agreement_with_invoices(admin_client, test_engine)
    a = await admin_client.post(PA_URL, json={
        "title": "A", "agreement_id": str(agr.id), "invoice_ids": [invs[0]["id"]],
        "subtotal": "100.00", "tax_amount": "0.00"})
    assert a.status_code == 201, a.text
    b = await admin_client.post(PA_URL, json={
        "title": "B", "agreement_id": str(agr.id), "invoice_ids": [invs[1]["id"]],
        "subtotal": "100.00", "tax_amount": "0.00"})
    assert b.status_code == 201, b.text
    await _set(test_engine, PaymentApplication, b.json()["id"], status="returned")

    r = await admin_client.patch(f"{PA_URL}/{b.json()['id']}",
                                 json={"invoice_ids": [invs[1]["id"], invs[0]["id"]]})
    assert r.status_code == 409, r.text
    assert a.json()["pa_number"] in r.json()["detail"]


@pytest.mark.asyncio
async def test_cancelled_pa_releases_its_invoice(admin_client, test_engine):
    po, invs = await _po_with_invoices(admin_client, test_engine, [_date(2026, 3, 2)])
    r = await admin_client.post(PA_URL, json=_pa_body(po["id"], invs))
    assert r.status_code == 201, r.text
    await _set(test_engine, PaymentApplication, r.json()["id"], status="cancelled")
    r = await admin_client.post(PA_URL, json=_pa_body(po["id"], invs))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_submit_refuses_a_duplicate_saved_before_the_check(admin_client, test_engine):
    po, invs = await _po_with_invoices(admin_client, test_engine, [_date(2026, 3, 2)] * 2)
    a = (await admin_client.post(PA_URL, json=_pa_body(po["id"], [invs[0]]))).json()
    b = (await admin_client.post(PA_URL, json=_pa_body(po["id"], [invs[1]]))).json()
    await _set(test_engine, PaymentApplication, b["id"], invoice_ids=invs)  # legacy duplicate
    r = await admin_client.post(f"{PA_URL}/{b['id']}/action", json={"action": "submit"})
    assert r.status_code == 409, r.text
    assert a["pa_number"] in r.json()["detail"]


# ── ISS-0125 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invoice_on_live_pa_amounts_locked_dates_editable(admin_client, test_engine):
    po, invs = await _po_with_invoices(admin_client, test_engine, [_date(2026, 3, 2)])
    pa = (await admin_client.post(PA_URL, json=_pa_body(po["id"], invs))).json()

    r = await admin_client.patch(f"{INV_URL}/{invs[0]}", json={"amount": "105.00"})
    assert r.status_code == 409, r.text
    assert pa["pa_number"] in r.json()["detail"] and "pre-tax amount" in r.json()["detail"]

    # Re-sending the same figures (the Edit form sends every field) is not a change.
    r = await admin_client.patch(f"{INV_URL}/{invs[0]}", json={
        "amount": "100.00", "tax_amount": "13.00", "due_date": "2026-03-05"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "matched"


@pytest.mark.asyncio
async def test_invoice_on_live_pa_cannot_be_deleted(admin_client, test_engine):
    po, invs = await _po_with_invoices(admin_client, test_engine, [_date(2026, 3, 2)])
    pa = (await admin_client.post(PA_URL, json=_pa_body(po["id"], invs))).json()
    # The state ISS-0125 reached through an edit: unmatched while on the PA.
    await _set(test_engine, Invoice, invs[0], status="unmatched")
    r = await admin_client.delete(f"{INV_URL}/{invs[0]}")
    assert r.status_code == 409, r.text
    assert pa["pa_number"] in r.json()["detail"]


@pytest.mark.asyncio
async def test_pa_whose_invoice_was_deleted_cannot_be_submitted(admin_client, test_engine):
    po, invs = await _po_with_invoices(admin_client, test_engine, [_date(2026, 3, 2)])
    pa = (await admin_client.post(PA_URL, json=_pa_body(po["id"], invs))).json()
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = (await db.execute(select(Invoice).where(Invoice.id == _uuid.UUID(invs[0])))).scalar_one()
        await db.delete(row)
        await db.commit()
    r = await admin_client.post(f"{PA_URL}/{pa['id']}/action", json={"action": "submit"})
    assert r.status_code == 409, r.text
    assert "has been deleted" in r.json()["detail"]


# ── ISS-0120 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_match_to_another_vendors_po_refused(admin_client):
    await _ensure_company_config()
    mine = await _make_vendor(admin_client, f"B1-MINE-{_uuid.uuid4().hex[:6]}")
    other = await _make_vendor(admin_client, f"B1-OTHR-{_uuid.uuid4().hex[:6]}")
    other_po = await _make_issued_po(admin_client, other["id"])
    inv = (await admin_client.post(INV_URL, json=_inv_payload(
        mine["id"], vendor_invoice_number=f"B1-{_uuid.uuid4().hex[:6]}"))).json()

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"allocations": [
        {"invoice_line_id": inv["line_items"][0]["id"], "po_id": other_po["id"],
         "po_line_id": other_po["line_items"][0]["id"], "allocated_amount": "1000.00"}]})
    assert r.status_code == 422, r.text
    assert other_po["number"] in r.json()["detail"]

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={"po_id": other_po["id"]})
    assert r.status_code == 422, r.text


# ── ISS-0109 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agreement_pa_takes_the_invoice_lines(admin_client, test_engine):
    agr, invs = await _agreement_with_invoices(admin_client, test_engine)
    r = await admin_client.post(PA_URL, json={
        "title": "Lines", "agreement_id": str(agr.id), "invoice_ids": [invs[0]["id"]],
        "subtotal": "100.00", "tax_amount": "0.00"})
    assert r.status_code == 201, r.text
    lines = r.json()["line_items"]
    assert [(l["description"], Decimal(str(l["line_total"]))) for l in lines] == [
        ("Monthly statement", Decimal("100.00"))]
    assert invs[0]["internal_ref"] in lines[0]["notes"]

    # Changing the invoice selection on a draft rebuilds them.
    r = await admin_client.patch(f"{PA_URL}/{r.json()['id']}",
                                 json={"invoice_ids": [i["id"] for i in invs]})
    assert r.status_code == 200, r.text
    assert len(r.json()["line_items"]) == 2


@pytest.mark.asyncio
async def test_po_header_match_without_picked_lines_takes_invoice_lines(admin_client, test_engine):
    po, invs = await _po_with_invoices(admin_client, test_engine, [_date(2026, 3, 2)])
    # _po_with_invoices seeds invoices with no lines: one line for the invoice.
    r = await admin_client.post(PA_URL, json=_pa_body(po["id"], invs, line_items=[]))
    assert r.status_code == 201, r.text
    assert [Decimal(str(l["line_total"])) for l in r.json()["line_items"]] == [Decimal("100.00")]
