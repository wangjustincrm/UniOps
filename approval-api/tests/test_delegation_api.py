"""Admin REST surface for delegations (代班) — system_admin only.

Mirrors test_routing_api.py's authenticated-client pattern (NEW HTTP-level
infrastructure for approval-api). Model-level constraint behaviour (self-
delegation, date order, overlap, revoked-frees-dates) is already covered by
test_delegation_model.py; this file asserts the HTTP surface translates those
into the right status codes instead of leaking a 500.
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.user import User

pytestmark = pytest.mark.asyncio
BASE = "/approval/v1/delegations"

START, END = "2026-08-20", "2026-09-03"


def _token(role: str, sub: str | None = None) -> str:
    return jose_jwt.encode(
        {"sub": sub or str(uuid.uuid4()), "role": role},
        settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _client(role="system_admin", sub=None):
    """Authed client — same pattern as test_routing_api.py."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {_token(role, sub)}"})


@pytest.fixture(autouse=True)
def _override_get_db(engine_db_session):
    async def _get_db_override():
        yield engine_db_session
    app.dependency_overrides[get_db] = _get_db_override
    yield
    app.dependency_overrides.pop(get_db, None)


async def _mk_user(db, name="Test User"):
    u = User(id=uuid.uuid4(), full_name=name, role="dept_manager", is_active=True)
    db.add(u)
    await db.flush()
    return u


# 1. A non-admin gets 403 on every verb.
async def test_non_admin_gets_403_on_every_verb(engine_db_session):
    db = engine_db_session
    delegator = await _mk_user(db, "Delegator")
    delegate = await _mk_user(db, "Delegate")
    did = uuid.uuid4()
    body = {"delegator_user_id": str(delegator.id), "delegate_user_id": str(delegate.id),
            "start_date": START, "end_date": END}
    async with _client("requester") as c:
        assert (await c.get(BASE)).status_code == 403
        assert (await c.post(BASE, json=body)).status_code == 403
        assert (await c.patch(f"{BASE}/{did}", json={"note": "x"})).status_code == 403
        assert (await c.post(f"{BASE}/{did}/revoke")).status_code == 403


# 2. POST with delegator == delegate -> 422
async def test_post_rejects_self_delegation(engine_db_session):
    db = engine_db_session
    delegator = await _mk_user(db, "Solo")
    body = {"delegator_user_id": str(delegator.id), "delegate_user_id": str(delegator.id),
            "start_date": START, "end_date": END}
    async with _client() as c:
        r = await c.post(BASE, json=body)
    assert r.status_code == 422


# 3. POST with end_date < start_date -> 422
async def test_post_rejects_end_before_start(engine_db_session):
    db = engine_db_session
    delegator = await _mk_user(db, "Delegator")
    delegate = await _mk_user(db, "Delegate")
    body = {"delegator_user_id": str(delegator.id), "delegate_user_id": str(delegate.id),
            "start_date": END, "end_date": START}
    async with _client() as c:
        r = await c.post(BASE, json=body)
    assert r.status_code == 422


# 4. POST overlapping a live window -> 409 with a readable message, NOT a 500
#    leaking the constraint name.
async def test_post_overlapping_window_returns_409_with_readable_message(engine_db_session):
    db = engine_db_session
    delegator = await _mk_user(db, "Delegator")
    delegate_a = await _mk_user(db, "Delegate A")
    delegate_b = await _mk_user(db, "Delegate B")
    body1 = {"delegator_user_id": str(delegator.id), "delegate_user_id": str(delegate_a.id),
             "start_date": START, "end_date": END}
    body2 = {"delegator_user_id": str(delegator.id), "delegate_user_id": str(delegate_b.id),
             "start_date": "2026-09-01", "end_date": "2026-09-10"}
    async with _client() as c:
        r1 = await c.post(BASE, json=body1)
        assert r1.status_code == 201
        r2 = await c.post(BASE, json=body2)
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    assert "ex_delegation_no_overlap" not in detail
    assert "gist" not in detail.lower()
    assert "delegation" in detail.lower()


# 5. POST after the first was revoked -> 201
async def test_post_after_first_revoked_succeeds(engine_db_session):
    db = engine_db_session
    delegator = await _mk_user(db, "Delegator")
    delegate_a = await _mk_user(db, "Delegate A")
    delegate_b = await _mk_user(db, "Delegate B")
    body1 = {"delegator_user_id": str(delegator.id), "delegate_user_id": str(delegate_a.id),
             "start_date": START, "end_date": END}
    async with _client() as c:
        r1 = await c.post(BASE, json=body1)
        assert r1.status_code == 201
        first_id = r1.json()["id"]

        rr = await c.post(f"{BASE}/{first_id}/revoke")
        assert rr.status_code == 200

        body2 = {"delegator_user_id": str(delegator.id), "delegate_user_id": str(delegate_b.id),
                 "start_date": "2026-09-01", "end_date": "2026-09-10"}
        r2 = await c.post(BASE, json=body2)
    assert r2.status_code == 201


# 6. GET returns the rows with delegator/delegate full names resolved.
async def test_get_resolves_full_names(engine_db_session):
    db = engine_db_session
    delegator = await _mk_user(db, "Grace Delegator")
    delegate = await _mk_user(db, "Henry Delegate")
    body = {"delegator_user_id": str(delegator.id), "delegate_user_id": str(delegate.id),
            "start_date": START, "end_date": END}
    async with _client() as c:
        assert (await c.post(BASE, json=body)).status_code == 201
        r = await c.get(BASE)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["delegator_name"] == "Grace Delegator"
    assert rows[0]["delegate_name"] == "Henry Delegate"


# 7. revoke sets revoked_at and revoked_by, and a second revoke is a no-op 200.
async def test_revoke_sets_fields_and_is_idempotent(engine_db_session):
    db = engine_db_session
    delegator = await _mk_user(db, "Delegator")
    delegate = await _mk_user(db, "Delegate")
    body = {"delegator_user_id": str(delegator.id), "delegate_user_id": str(delegate.id),
            "start_date": START, "end_date": END}
    admin_id = str(uuid.uuid4())
    async with _client("system_admin", sub=admin_id) as c:
        r1 = await c.post(BASE, json=body)
        assert r1.status_code == 201
        did = r1.json()["id"]

        r2 = await c.post(f"{BASE}/{did}/revoke")
        assert r2.status_code == 200
        assert r2.json()["revoked_at"] is not None
        assert r2.json()["revoked_by"] == admin_id
        first_revoked_at = r2.json()["revoked_at"]

        r3 = await c.post(f"{BASE}/{did}/revoke")
        assert r3.status_code == 200
        assert r3.json()["revoked_at"] == first_revoked_at
        assert r3.json()["revoked_by"] == admin_id
