"""Tests for dept_director_mapping config field."""
import pytest

CONFIG_URL = "/api/v1/config"


@pytest.mark.asyncio
async def test_update_dept_director_mapping_roundtrips(admin_client):
    dept = "11111111-1111-1111-1111-111111111111"
    user = "22222222-2222-2222-2222-222222222222"
    resp = await admin_client.patch(CONFIG_URL, json={"dept_director_mapping": {dept: user}})
    assert resp.status_code == 200
    got = await admin_client.get(CONFIG_URL)
    assert got.json()["dept_director_mapping"] == {dept: user}
