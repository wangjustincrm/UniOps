"""Visitor CRUD endpoints (PRD §6.5.1)."""
import uuid

import pytest


# ── Sample payload ──────────────────────────────────────────────────────────-

_VISITOR_BASE = {
    "first_name": "John",
    "last_name":  "Smith",
    "company_name": "ABC Corp",
    "phone": "+1-555-0100",
    "email": "john.smith@abc.example.com",
    "visitor_type": "supplier",
    "id_verified": False,
}


# ── Open access for any UniOps role ─────────────────────────────────────────-

@pytest.mark.asyncio
async def test_requester_can_create_visitor(requester):
    _, client = requester
    resp = await client.post("/api/v1/visitors", json=_VISITOR_BASE)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["first_name"] == "John"
    assert body["visitor_type"] == "supplier"
    assert "id" in body


@pytest.mark.asyncio
async def test_create_visitor_without_company(requester):
    """Company is optional — interviewees / students often have none. It
    stores as "" (the column is NOT NULL)."""
    _, client = requester
    payload = {k: v for k, v in _VISITOR_BASE.items() if k != "company_name"}
    payload["last_name"] = "Student"
    resp = await client.post("/api/v1/visitors", json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["company_name"] == ""


@pytest.mark.asyncio
async def test_anonymous_rejected(client):
    resp = await client.post("/api/v1/visitors", json=_VISITOR_BASE)
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_get_existing_visitor(requester):
    _, client = requester
    created = (await client.post("/api/v1/visitors", json=_VISITOR_BASE)).json()
    got = await client.get(f"/api/v1/visitors/{created['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == created["id"]


@pytest.mark.asyncio
async def test_get_missing_visitor_404(requester):
    _, client = requester
    resp = await client.get(f"/api/v1/visitors/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_with_search(requester):
    _, client = requester
    marker = f"zsearch{uuid.uuid4().hex[:6]}"
    payload = {**_VISITOR_BASE, "company_name": f"AcmeCorp{marker}"}
    await client.post("/api/v1/visitors", json=payload)
    listed = await client.get(f"/api/v1/visitors?search={marker}")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert marker in body["items"][0]["company_name"]


@pytest.mark.asyncio
async def test_patch_updates_fields(requester):
    _, client = requester
    created = (await client.post("/api/v1/visitors", json=_VISITOR_BASE)).json()
    resp = await client.patch(
        f"/api/v1/visitors/{created['id']}",
        json={"job_title": "Plant Engineer", "id_verified": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job_title"] == "Plant Engineer"
    assert body["id_verified"] is True
    # Untouched fields preserved.
    assert body["first_name"] == "John"
