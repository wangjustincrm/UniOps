"""ISS-0164: adding, editing and deactivating tax codes is System Admin only
(not the mdm.finance.write key, which still gates the other master data).
Reading stays open to every role."""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient


_NEW = {"code": "TST13", "name": "Test 13%", "tax_type": "HST", "province": "ON",
        "rate": "0.13", "effective_from": "2026-01-01"}


async def _client_as(db_session, role):
    from app.main import app
    from app.db.base import get_db
    from app.core.deps import get_token_payload

    async def _db():
        yield db_session

    async def _user():
        return {"sub": str(uuid.uuid4()), "role": role, "type": "access"}

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_token_payload] = _user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("role", ["finance_manager", "ap_clerk", "payment_officer"])
async def test_non_admin_cannot_write_tax_codes(db_session, role):
    from app.main import app
    async with await _client_as(db_session, role) as c:
        assert (await c.post("/mdm/v1/tax/codes", json=_NEW)).status_code == 403
        assert (await c.patch(f"/mdm/v1/tax/codes/{uuid.uuid4()}", json={"name": "x"})).status_code == 403
        assert (await c.delete(f"/mdm/v1/tax/codes/{uuid.uuid4()}")).status_code == 403
        assert (await c.get("/mdm/v1/tax/codes")).status_code == 200
    app.dependency_overrides.clear()


async def test_system_admin_can_write_tax_codes(db_session):
    from app.main import app
    async with await _client_as(db_session, "system_admin") as c:
        r = await c.post("/mdm/v1/tax/codes", json=_NEW)
        assert r.status_code == 201, r.text
        code_id = r.json()["id"]
        assert (await c.patch(f"/mdm/v1/tax/codes/{code_id}", json={"name": "Renamed"})).status_code == 200
        assert (await c.delete(f"/mdm/v1/tax/codes/{code_id}")).status_code == 200
    app.dependency_overrides.clear()
