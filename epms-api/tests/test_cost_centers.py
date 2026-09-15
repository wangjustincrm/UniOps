"""Tests for GET/POST/PATCH/DELETE /api/v1/cost-centers (added 2026-05-08)."""
import uuid
import pytest

URL = "/api/v1/cost-centers"
DEPT_URL = "/api/v1/departments"


async def _make_dept(client, code: str | None = None) -> dict:
    code = code or f"D{uuid.uuid4().hex[:8].upper()}"
    resp = await client.post(DEPT_URL, json={"code": code, "name": f"Dept {code}", "is_active": True})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_cc(client, dept_id: str, code: str | None = None) -> dict:
    code = code or f"CC{uuid.uuid4().hex[:8].upper()}"
    resp = await client.post(URL, json={"code": code, "name": f"CostCenter {code}", "department_id": dept_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── List ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_cost_centers(admin_client):
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_cost_centers_filter_by_department(admin_client):
    dept_a = await _make_dept(admin_client)
    dept_b = await _make_dept(admin_client)
    cc_a = await _make_cc(admin_client, dept_a["id"])
    await _make_cc(admin_client, dept_b["id"])

    resp = await admin_client.get(f"{URL}?department_id={dept_a['id']}")
    assert resp.status_code == 200
    ids = [x["id"] for x in resp.json()]
    assert cc_a["id"] in ids
    for x in resp.json():
        assert x["department_id"] == dept_a["id"]


# ── Create ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_cost_center(admin_client):
    dept = await _make_dept(admin_client)
    resp = await admin_client.post(URL, json={
        "code": f"GA-{uuid.uuid4().hex[:4].upper()}",
        "name": "IT Department",
        "department_id": dept["id"],
    })
    assert resp.status_code == 201
    assert resp.json()["department_id"] == dept["id"]


@pytest.mark.asyncio
async def test_create_cost_center_nonexistent_dept(admin_client):
    """Creating a CC against a non-existent department → 404."""
    resp = await admin_client.post(URL, json={
        "code": f"FAKE-{uuid.uuid4().hex[:4]}",
        "name": "Fake",
        "department_id": str(uuid.uuid4()),
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_cost_center_duplicate_code(admin_client):
    """Duplicate code → 409."""
    dept = await _make_dept(admin_client)
    unique_code = f"DUP{uuid.uuid4().hex[:4].upper()}"
    await _make_cc(admin_client, dept["id"], code=unique_code)
    resp = await admin_client.post(URL, json={
        "code": unique_code, "name": "Another", "department_id": dept["id"],
    })
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_cost_center_requires_admin(finance_client):
    """Only system_admin can create cost centers."""
    resp = await finance_client.post(URL, json={
        "code": "NO-PERM", "name": "Test", "department_id": str(uuid.uuid4()),
    })
    assert resp.status_code == 403


# ── Read ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_cost_center(admin_client):
    dept = await _make_dept(admin_client)
    cc = await _make_cc(admin_client, dept["id"])
    resp = await admin_client.get(f"{URL}/{cc['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == cc["id"]


@pytest.mark.asyncio
async def test_get_cost_center_not_found(admin_client):
    resp = await admin_client.get(f"{URL}/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── Update ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_cost_center(admin_client):
    dept = await _make_dept(admin_client)
    cc = await _make_cc(admin_client, dept["id"])
    resp = await admin_client.patch(f"{URL}/{cc['id']}", json={"name": "Renamed CC"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed CC"


@pytest.mark.asyncio
async def test_update_cost_center_nonexistent_dept(admin_client):
    dept = await _make_dept(admin_client)
    cc = await _make_cc(admin_client, dept["id"])
    resp = await admin_client.patch(f"{URL}/{cc['id']}", json={"department_id": str(uuid.uuid4())})
    assert resp.status_code == 404


# ── Delete ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_cost_center(admin_client):
    dept = await _make_dept(admin_client)
    cc = await _make_cc(admin_client, dept["id"])
    resp = await admin_client.delete(f"{URL}/{cc['id']}")
    assert resp.status_code == 204
    assert (await admin_client.get(f"{URL}/{cc['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_delete_cost_center_with_pr_references(admin_client):
    """PRD — CC referenced by PRs cannot be deleted (409); must deactivate instead."""
    dept = await _make_dept(admin_client)
    cc = await _make_cc(admin_client, dept["id"])

    pr_resp = await admin_client.post("/api/v1/pr", json={
        "title": "CC delete test PR",
        "type": 3,
        "currency": "CAD",
        "cost_center_id": cc["id"],
        "line_items": [
            {"description": "Part X", "material_id": "M-X01", "qty": "1",
             "unit": "EA", "unit_price": "10.00"}
        ],
    })
    if pr_resp.status_code == 201:
        del_resp = await admin_client.delete(f"{URL}/{cc['id']}")
        assert del_resp.status_code == 409
        assert "deactivate" in del_resp.json()["detail"].lower()
