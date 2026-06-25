"""Project endpoint tests."""
import pytest

URL = "/api/v1/projects"


def _payload(**overrides):
    base = {
        "code": "PROJ-2026-001",
        "name": "Factory Upgrade",
        "status": "active",
        "budget": "500000.00",
        "currency": "CAD",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "owner_dept": "OPS",
        "manager_name": "Jane Doe",
    }
    base.update(overrides)
    return base


async def _create(client, **overrides):
    resp = await client.post(URL, json=_payload(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_list_projects(admin_client):
    resp = await admin_client.get(URL)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_project(admin_client):
    p = await _create(admin_client, code="PROJ-TST-01")
    assert p["code"] == "PROJ-TST-01"
    assert p["status"] == "active"
    assert float(p["budget"]) == 500000.0


@pytest.mark.asyncio
async def test_create_project_duplicate(admin_client):
    await _create(admin_client, code="PROJ-DUP-01")
    resp = await admin_client.post(URL, json=_payload(code="PROJ-DUP-01"))
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_project(admin_client):
    p = await _create(admin_client, code="PROJ-GET-01")
    resp = await admin_client.get(f"{URL}/{p['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == p["id"]


@pytest.mark.asyncio
async def test_update_project(admin_client):
    p = await _create(admin_client, code="PROJ-UPD-01")
    resp = await admin_client.patch(f"{URL}/{p['id']}", json={"status": "on_hold"})
    assert resp.json()["status"] == "on_hold"


@pytest.mark.asyncio
async def test_create_requires_admin(client):
    resp = await client.post(URL, json=_payload(code="PROJ-UNAUTH"))
    assert resp.status_code == 403
