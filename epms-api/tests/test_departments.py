"""Department + CostCenter endpoint tests."""
import pytest

DEPT_URL = "/api/v1/departments"
CC_URL = "/api/v1/cost-centers"


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _create_dept(client, code="DEPT-TEST", name="Test Dept"):
    resp = await client.post(DEPT_URL, json={"code": code, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Department CRUD ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_departments_empty(admin_client):
    resp = await admin_client.get(DEPT_URL)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_create_department(admin_client):
    dept = await _create_dept(admin_client, "MKT", "Marketing")
    assert dept["code"] == "MKT"
    assert dept["name"] == "Marketing"
    assert dept["is_active"] is True
    assert dept["cost_centers"] == []


@pytest.mark.asyncio
async def test_create_department_duplicate_code(admin_client):
    await _create_dept(admin_client, "DUP-DEPT", "Dup Dept")
    resp = await admin_client.post(DEPT_URL, json={"code": "dup-dept", "name": "Dup Dept 2"})
    assert resp.status_code == 409  # codes are uppercased → duplicate


@pytest.mark.asyncio
async def test_get_department_by_id(admin_client):
    dept = await _create_dept(admin_client, "IT-TST", "IT Test")
    resp = await admin_client.get(f"{DEPT_URL}/{dept['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == dept["id"]


@pytest.mark.asyncio
async def test_get_department_not_found(admin_client):
    resp = await admin_client.get(f"{DEPT_URL}/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_department(admin_client):
    dept = await _create_dept(admin_client, "UPD-DEPT", "Old Name")
    resp = await admin_client.patch(f"{DEPT_URL}/{dept['id']}", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_deactivate_department(admin_client):
    dept = await _create_dept(admin_client, "DEACT-DEPT", "Deactivate Me")
    resp = await admin_client.patch(f"{DEPT_URL}/{dept['id']}", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_create_department_requires_admin(client):
    """Unauthenticated request should be rejected."""
    resp = await client.post(DEPT_URL, json={"code": "UNAUTH", "name": "Unauth"})
    assert resp.status_code == 403


# ── CostCenter CRUD ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_cost_center(admin_client):
    dept = await _create_dept(admin_client, "FIN-CC", "Finance CC")
    resp = await admin_client.post(CC_URL, json={
        "code": "CC-FIN-01",
        "name": "Finance Operations",
        "department_id": dept["id"],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["code"] == "CC-FIN-01"
    assert data["department_id"] == dept["id"]


@pytest.mark.asyncio
async def test_create_cost_center_unknown_dept(admin_client):
    resp = await admin_client.post(CC_URL, json={
        "code": "CC-NODEPT",
        "name": "No Dept CC",
        "department_id": "00000000-0000-0000-0000-000000000000",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_cost_centers_filtered(admin_client):
    dept = await _create_dept(admin_client, "OPS-CC", "Ops CC")
    await admin_client.post(CC_URL, json={
        "code": "CC-OPS-01", "name": "Ops 1", "department_id": dept["id"]
    })
    await admin_client.post(CC_URL, json={
        "code": "CC-OPS-02", "name": "Ops 2", "department_id": dept["id"]
    })

    resp = await admin_client.get(CC_URL, params={"department_id": dept["id"]})
    assert resp.status_code == 200
    codes = [cc["code"] for cc in resp.json()]
    assert "CC-OPS-01" in codes
    assert "CC-OPS-02" in codes


@pytest.mark.asyncio
async def test_update_cost_center(admin_client):
    dept = await _create_dept(admin_client, "HR-UPD", "HR Upd")
    cc_resp = await admin_client.post(CC_URL, json={
        "code": "CC-HR-UPD", "name": "HR Old", "department_id": dept["id"]
    })
    cc_id = cc_resp.json()["id"]

    resp = await admin_client.patch(f"{CC_URL}/{cc_id}", json={"name": "HR New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "HR New"


@pytest.mark.asyncio
async def test_department_response_includes_cost_centers(admin_client):
    dept = await _create_dept(admin_client, "PROC-CC", "Procurement CC")
    await admin_client.post(CC_URL, json={
        "code": "CC-PROC-01", "name": "Proc 1", "department_id": dept["id"]
    })

    resp = await admin_client.get(f"{DEPT_URL}/{dept['id']}")
    assert resp.status_code == 200
    cc_codes = [cc["code"] for cc in resp.json()["cost_centers"]]
    assert "CC-PROC-01" in cc_codes
