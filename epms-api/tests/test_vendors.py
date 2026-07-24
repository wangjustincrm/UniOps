"""Vendor endpoint tests."""
import pytest

URL = "/api/v1/vendors"


def _payload(**overrides):
    base = {
        "code": "VENDOR-01",
        "name": "Acme Supply Co.",
        "category": "Raw Materials",
        "contact_name": "John Smith",
        "contact_email": "john@acme.com",
        "phone": "+1-555-0100",
        "payment_terms": "net30",
        "currency": "CAD",
    }
    base.update(overrides)
    return base


async def _create(client, **overrides):
    resp = await client.post(URL, json=_payload(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_list_vendors_empty(admin_client):
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["items"], list)


@pytest.mark.asyncio
async def test_create_vendor(admin_client):
    v = await _create(admin_client, code="VND-CREATE-01")
    assert v["code"] == "VND-CREATE-01"
    assert v["is_active"] is True
    assert v["payment_terms"] == "net30"


@pytest.mark.asyncio
async def test_create_vendor_without_contact(admin_client):
    """Contact name/email are optional in the UI — omitting them (or sending
    empty strings) must still create the vendor, not 422. Regression: the
    schema required min_length=1, so vendors saved without a contact silently
    failed while the frontend swallowed the error."""
    resp = await admin_client.post(
        URL,
        json=_payload(code="VND-NOCONTACT-01", contact_name="", contact_email=""),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["code"] == "VND-NOCONTACT-01"


@pytest.mark.asyncio
async def test_create_vendor_duplicate_code(admin_client):
    await _create(admin_client, code="VND-DUP-01")
    # mdm (the owner) enforces exact-code uniqueness
    resp = await admin_client.post(URL, json=_payload(code="VND-DUP-01"))
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_vendor(admin_client):
    v = await _create(admin_client, code="VND-GET-01")
    resp = await admin_client.get(f"{URL}/{v['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == v["id"]


@pytest.mark.asyncio
async def test_update_vendor(admin_client):
    v = await _create(admin_client, code="VND-UPD-01", name="Old Name")
    resp = await admin_client.patch(f"{URL}/{v['id']}", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_update_vendor_code(admin_client):
    """POID (vendor code) edits must persist — schema used to drop `code` silently."""
    v = await _create(admin_client, code="VND-POID-OLD")
    resp = await admin_client.patch(f"{URL}/{v['id']}", json={"code": "VND-POID-NEW"})
    assert resp.status_code == 200
    assert resp.json()["code"] == "VND-POID-NEW"


@pytest.mark.asyncio
async def test_deactivate_vendor(admin_client):
    v = await _create(admin_client, code="VND-DEACT-01")
    resp = await admin_client.patch(f"{URL}/{v['id']}", json={"is_active": False})
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_list_filter_active(admin_client):
    await _create(admin_client, code="VND-ACT-01")
    v2 = await _create(admin_client, code="VND-INACT-01")
    await admin_client.patch(f"{URL}/{v2['id']}", json={"is_active": False})
    resp = await admin_client.get(URL, params={"active_only": True})
    codes = [v["code"] for v in resp.json()["items"]]
    assert "VND-ACT-01" in codes
    assert "VND-INACT-01" not in codes


@pytest.mark.asyncio
async def test_create_requires_auth(client):
    resp = await client.post(URL, json=_payload(code="VND-UNAUTH"))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_vendor_with_remittance_email(admin_client):
    """remittance_email must round-trip through the mdm forwarder and back
    through the local vendor read path (business_partners, not the compat
    view) — this is the field the whole task exists to carry."""
    v = await _create(admin_client, code="VND-REMIT-01", remittance_email="remit@acme.com")
    assert v["remittance_email"] == "remit@acme.com"

    resp = await admin_client.get(f"{URL}/{v['id']}")
    assert resp.json()["remittance_email"] == "remit@acme.com"


@pytest.mark.asyncio
async def test_update_vendor_remittance_email(admin_client):
    v = await _create(admin_client, code="VND-REMIT-02")
    resp = await admin_client.patch(f"{URL}/{v['id']}", json={"remittance_email": "ap2@acme.com"})
    assert resp.status_code == 200
    assert resp.json()["remittance_email"] == "ap2@acme.com"


@pytest.mark.asyncio
async def test_import_csv_carries_remittance_email(admin_client):
    csv_body = (
        "code,name,category,contactName,contactEmail,remittanceEmail\n"
        "VND-CSV-REMIT-01,CSV Vendor,Raw Materials,Jane,jane@csv.test,remit@csv.test\n"
    )
    files = {"file": ("vendors.csv", csv_body, "text/csv")}
    resp = await admin_client.post(f"{URL}/import", files=files)
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1

    listing = await admin_client.get(URL, params={"search": "VND-CSV-REMIT-01"})
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["remittance_email"] == "remit@csv.test"


@pytest.mark.asyncio
async def test_export_csv_includes_remittance_email(admin_client):
    await _create(admin_client, code="VND-EXPORT-REMIT-01", remittance_email="remit@export.test")
    resp = await admin_client.get(f"{URL}/export")
    assert resp.status_code == 200
    header = resp.text.splitlines()[0]
    assert "remittanceEmail" in header
    assert "remit@export.test" in resp.text
