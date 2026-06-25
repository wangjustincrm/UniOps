"""Parts Catalog endpoint tests."""
import pytest

URL = "/api/v1/parts"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _payload(**overrides):
    base = {
        "code": "PART-0001",
        "category": "Bearings",
        "name": "Deep Groove Ball Bearing 6205",
        "description": "25x52x15mm",
        "supplier": "SKF",
        "supplier_part_no": "6205-2RS",
        "supplier_item_id": None,
        "unit_price": "12.50",
        "unit": "EA",
    }
    base.update(overrides)
    return base


async def _create(client, **overrides):
    resp = await client.post(URL, json=_payload(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── CRUD ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_parts_empty(admin_client):
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_create_part(admin_client):
    part = await _create(admin_client, code="PART-CREATE-01")
    assert part["code"] == "PART-CREATE-01"
    assert part["category"] == "Bearings"
    assert part["is_active"] is True
    assert float(part["unit_price"]) == 12.50


@pytest.mark.asyncio
async def test_create_part_duplicate_code(admin_client):
    await _create(admin_client, code="PART-DUP-01")
    resp = await admin_client.post(URL, json=_payload(code="part-dup-01"))
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_part_by_id(admin_client):
    part = await _create(admin_client, code="PART-GET-01")
    resp = await admin_client.get(f"{URL}/{part['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == part["id"]


@pytest.mark.asyncio
async def test_get_part_not_found(admin_client):
    resp = await admin_client.get(f"{URL}/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_part(admin_client):
    part = await _create(admin_client, code="PART-UPD-01", name="Old Name")
    resp = await admin_client.patch(f"{URL}/{part['id']}", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_deactivate_part(admin_client):
    part = await _create(admin_client, code="PART-DEACT-01")
    resp = await admin_client.patch(f"{URL}/{part['id']}", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_delete_part(admin_client):
    part = await _create(admin_client, code="PART-DEL-01")
    resp = await admin_client.delete(f"{URL}/{part['id']}")
    assert resp.status_code == 204
    resp2 = await admin_client.get(f"{URL}/{part['id']}")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_list_filter_by_category(admin_client):
    await _create(admin_client, code="PART-CAT-F", category="Filters")
    await _create(admin_client, code="PART-CAT-B", category="Bearings-X")
    resp = await admin_client.get(URL, params={"category": "Filters"})
    assert resp.status_code == 200
    codes = [p["code"] for p in resp.json()]
    assert "PART-CAT-F" in codes
    assert "PART-CAT-B" not in codes


@pytest.mark.asyncio
async def test_list_search(admin_client):
    await _create(admin_client, code="PART-SRCH-01", name="Unique Sprocket Widget")
    resp = await admin_client.get(URL, params={"search": "Sprocket"})
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()]
    assert any("Sprocket" in n for n in names)


@pytest.mark.asyncio
async def test_create_requires_auth(client):
    resp = await client.post(URL, json=_payload(code="PART-UNAUTH"))
    assert resp.status_code == 403


# ── CSV import / export ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_csv_import(admin_client):
    csv_data = (
        "code,category,name,description,supplier,supplier_part_no,supplier_item_id,unit_price,unit\n"
        "PART-IMP-01,Seals,Oil Seal 40x60x10,,NOK,TC-40-60-10,,8.75,EA\n"
        "PART-IMP-02,Filters,Air Filter,,Mann,C25114/1,,22.00,EA\n"
    )
    resp = await admin_client.post(
        f"{URL}/import",
        files={"file": ("parts.csv", csv_data.encode(), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 2
    assert data["updated"] == 0
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_csv_import_upsert(admin_client):
    csv_create = (
        "code,category,name,description,supplier,supplier_part_no,supplier_item_id,unit_price,unit\n"
        "PART-UPS-01,Electrical,Relay 24V,,Omron,MY2N-J,,15.00,EA\n"
    )
    await admin_client.post(
        f"{URL}/import",
        files={"file": ("p.csv", csv_create.encode(), "text/csv")},
    )
    csv_update = (
        "code,category,name,description,supplier,supplier_part_no,supplier_item_id,unit_price,unit\n"
        "PART-UPS-01,Electrical,Relay 24V DC,,Omron,MY2N-J,,17.50,EA\n"
    )
    resp = await admin_client.post(
        f"{URL}/import",
        files={"file": ("p.csv", csv_update.encode(), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 0
    assert data["updated"] == 1


@pytest.mark.asyncio
async def test_csv_export(admin_client):
    await _create(admin_client, code="PART-EXP-01", name="Export Test Part")
    resp = await admin_client.get(f"{URL}/export")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "PART-EXP-01" in resp.text
