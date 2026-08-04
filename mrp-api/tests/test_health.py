import pytest


@pytest.mark.anyio
async def test_health(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
