"""ISS-0267: a PO cannot be raised on, or submitted with, an Inactive vendor
(PRD VND-002, PO-006). The PR's Create PO pre-fills the PR's vendor even when
it was deactivated while the PR was in approval."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

PO_URL = "/api/v1/po"
VENDOR_URL = "/api/v1/vendors"
_LINE = {"description": "Widget", "qty": "1", "unit": "EA", "unit_price": "10.00"}


async def _vendor(client):
    r = await client.post(VENDOR_URL, json={
        "code": f"IV-{uuid.uuid4().hex[:8].upper()}", "name": f"Inactive Vendor {uuid.uuid4().hex[:6]}",
        "category": "Parts", "contact_name": "X", "contact_email": "x@x.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    r.raise_for_status()
    return r.json()


async def _po(client, vendor_id):
    return await client.post(PO_URL, json={
        "title": "Inactive vendor PO", "type": 2, "vendor_id": vendor_id, "currency": "CAD",
        "line_items": [_LINE],
    })


async def _deactivate(client, vendor_id):
    r = await client.patch(f"{VENDOR_URL}/{vendor_id}", json={"is_active": False})
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False


@pytest.mark.asyncio
async def test_submit_is_refused_when_the_vendor_became_inactive(admin_client):
    v = await _vendor(admin_client)
    po = (await _po(admin_client, v["id"])).json()       # draft while still active
    await _deactivate(admin_client, v["id"])

    with patch("app.api.v1.po.delegate_action", new_callable=AsyncMock) as engine:
        r = await admin_client.post(f"{PO_URL}/{po['id']}/action", json={"action": "submit"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == f"Vendor {v['name']} is inactive. Choose another vendor."
    engine.assert_not_called()

    # Other actions are not this rule's business (e.g. cancelling the draft).
    with patch("app.api.v1.po.delegate_action", new_callable=AsyncMock) as engine:
        r = await admin_client.post(f"{PO_URL}/{po['id']}/action", json={"action": "cancel"})
    engine.assert_called_once()


@pytest.mark.asyncio
async def test_submit_with_an_active_vendor_reaches_the_engine(admin_client):
    v = await _vendor(admin_client)
    po = (await _po(admin_client, v["id"])).json()
    with patch("app.api.v1.po.delegate_action", new_callable=AsyncMock) as engine:
        await admin_client.post(f"{PO_URL}/{po['id']}/action", json={"action": "submit"})
    engine.assert_called_once()


@pytest.mark.asyncio
async def test_a_new_po_cannot_be_raised_on_an_inactive_vendor(admin_client):
    v = await _vendor(admin_client)
    await _deactivate(admin_client, v["id"])
    r = await _po(admin_client, v["id"])
    assert r.status_code == 409, r.text
    assert "is inactive. Choose another vendor." in r.json()["detail"]


@pytest.mark.asyncio
async def test_edit_refuses_switching_to_an_inactive_vendor_but_keeps_the_current_one(admin_client):
    active = await _vendor(admin_client)
    other = await _vendor(admin_client)
    po = (await _po(admin_client, active["id"])).json()
    await _deactivate(admin_client, other["id"])

    r = await admin_client.patch(f"{PO_URL}/{po['id']}", json={"vendor_id": other["id"]})
    assert r.status_code == 409, r.text

    # The edit form re-sends the PO's own vendor on every save: still savable
    # as a draft after that vendor is deactivated (submit is where PO-006 bites).
    await _deactivate(admin_client, active["id"])
    r = await admin_client.patch(f"{PO_URL}/{po['id']}", json={"vendor_id": active["id"], "title": "Renamed"})
    assert r.status_code == 200, r.text
