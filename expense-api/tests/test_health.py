import pytest


@pytest.mark.asyncio
async def test_health_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_unauthenticated_expense_list(client):
    """PRD §3.1 — every OA endpoint requires a valid JWT."""
    resp = await client.get("/api/v1/expenses")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_pa_list(client):
    resp = await client.get("/api/v1/pa")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_policy(client):
    resp = await client.get("/api/v1/policy")
    assert resp.status_code == 403
