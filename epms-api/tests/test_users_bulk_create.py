"""Staff → Create users (EMPLOYEES.md UGEN-004)."""
import pytest

URL = "/api/v1/users/bulk-create"


def item(code="000052", email="qin@example.com", name="Peng Frank Qin"):
    return {"erp_person_code": code, "email": email, "full_name": name}


@pytest.mark.asyncio
async def test_creates_requesters_and_shows_the_password_once(admin_client):
    resp = await admin_client.post(URL, json={"items": [item(email="QIN@example.com ")]})
    assert resp.status_code == 200, resp.text
    [created] = resp.json()["created"]
    assert created["email"] == "qin@example.com" and created["temp_password"]
    users = (await admin_client.get("/api/v1/users?search=qin@example.com")).json()["items"]
    assert users[0]["role"] == "requester" and users[0]["erp_person_code"] == "000052"


@pytest.mark.asyncio
async def test_a_bad_row_is_reported_and_the_others_are_kept(admin_client):
    await admin_client.post(URL, json={"items": [item()]})
    resp = await admin_client.post(URL, json={"items": [
        item("000053", email=None),
        item("000054", email="qin@example.com"),
        item("000052", email="other@example.com"),
        item("000055", email="ok@example.com"),
    ]})
    body = resp.json()
    assert [c["email"] for c in body["created"]] == ["ok@example.com"]
    assert [e["erp_person_code"] for e in body["errors"]] == ["000053", "000054", "000052"]


@pytest.mark.asyncio
async def test_system_admin_only(client):
    resp = await client.post(URL, json={"items": [item()]})
    assert resp.status_code in (401, 403)
