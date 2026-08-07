"""PA created from an agreement-matched invoice (no PO, no GR)."""
import uuid
from decimal import Decimal

import pytest

from tests.test_agreement_invoice_match import _make_active_agreement, _upload_invoice
from tests.test_agreements import seed_vendor_and_user

pytestmark = pytest.mark.asyncio

PA_URL = "/api/v1/pa"


@pytest.fixture
async def agreement_matched_invoice(admin_client, test_engine):
    vendor_id, _vendor_name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _upload_invoice(admin_client, vendor_id, amount="1000.00")
    r = await admin_client.post(f"/api/v1/invoices/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
    assert r.status_code == 200, r.text
    return inv, agr


@pytest.fixture
async def unmatched_invoice(admin_client, test_engine):
    vendor_id, _vendor_name, _user_id = await seed_vendor_and_user(
        test_engine, vendor_name="Unmatched Vendor")
    return await _upload_invoice(admin_client, vendor_id, amount="50.00")


async def test_create_pa_from_agreement(admin_client, agreement_matched_invoice):
    inv, agr = agreement_matched_invoice
    r = await admin_client.post(PA_URL, json={
        "title": "Princess Auto July statement",
        "agreement_id": str(agr.id),
        "invoice_ids": [inv["id"]],
        "subtotal": "1000.00",
        "tax_amount": "130.00",
        "payment_amount": "1130.00",
        "line_items": [{"description": "July statement", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["agreement_id"] == str(agr.id)
    assert body["agreement_number"] == agr.number
    assert body["po_id"] is None
    assert body["status"] == "draft"


async def test_agreement_pa_skips_the_goods_receipt_gate(admin_client, agreement_matched_invoice):
    """No GR exists and none ever will for this route — the gate must not fire,
    and no receipt_override reason should be demanded."""
    inv, agr = agreement_matched_invoice
    r = await admin_client.post(PA_URL, json={
        "title": "No GR here",
        "agreement_id": str(agr.id),
        "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })
    assert r.status_code == 201, r.text
    assert r.json()["receipt_override"] is False


async def test_agreement_pa_appears_in_the_epms_list(admin_client, agreement_matched_invoice):
    """crud.pa.get_all filters on po_id IS NOT NULL to keep OA's Direct PAs out;
    that filter must not also hide agreement PAs."""
    inv, agr = agreement_matched_invoice
    created = (await admin_client.post(PA_URL, json={
        "title": "Listed?", "agreement_id": str(agr.id), "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })).json()

    listed = await admin_client.get(PA_URL)
    assert listed.status_code == 200
    assert created["id"] in [i["id"] for i in listed.json()["items"]]


async def test_pa_requires_exactly_one_source(admin_client, agreement_matched_invoice):
    inv, agr = agreement_matched_invoice
    neither = await admin_client.post(PA_URL, json={
        "title": "Neither", "invoice_ids": [inv["id"]],
        "subtotal": "1.00", "tax_amount": "0.00", "payment_amount": "1.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1.00", "line_total": "1.00"}],
    })
    assert neither.status_code == 422
    assert "po_id" in neither.text or "agreement_id" in neither.text


async def test_invoice_not_on_agreement_is_refused(admin_client, agreement_matched_invoice,
                                                   unmatched_invoice):
    _, agr = agreement_matched_invoice
    r = await admin_client.post(PA_URL, json={
        "title": "Wrong invoice", "agreement_id": str(agr.id),
        "invoice_ids": [unmatched_invoice["id"]],
        "subtotal": "1.00", "tax_amount": "0.00", "payment_amount": "1.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1.00", "line_total": "1.00"}],
    })
    assert r.status_code == 422
