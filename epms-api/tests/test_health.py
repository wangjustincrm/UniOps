"""Health endpoint tests."""
import pytest


@pytest.mark.asyncio
async def test_liveness(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "app" in data


@pytest.mark.asyncio
async def test_readiness(client):
    resp = await client.get("/api/v1/health/db")
    assert resp.status_code == 200
    assert resp.json()["database"] == "connected"
