"""Auth flow tests — relocated from epms-api with the implementation (B4).

Covers: login (plain + MFA via real Redis OTP), refresh rotation + blacklist,
logout, me, change-password, and the audit trail (FIN-AUD-001).
"""
import uuid

from sqlalchemy import select

from app.core.security import otp_redis_key
from app.models.audit import AuditLog
from tests.conftest import make_user

PWD = "TestPass1!"


async def _audit_actions(db_session, actor_id) -> list[str]:
    rows = (await db_session.execute(
        select(AuditLog.action).where(AuditLog.actor_id == actor_id)
    )).scalars().all()
    return list(rows)


async def test_login_without_mfa_returns_token_pair(client, test_engine, db_session):
    u = await make_user(test_engine)
    r = await client.post("/identity/v1/auth/login", json={"email": u.email, "password": PWD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body and "refresh_token" in body
    assert "login" in await _audit_actions(db_session, u.id)


async def test_login_wrong_password_401_and_audited(client, test_engine, db_session):
    u = await make_user(test_engine)
    r = await client.post("/identity/v1/auth/login", json={"email": u.email, "password": "wrong-pass"})
    assert r.status_code == 401
    failed = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "login_failed",
                               AuditLog.actor_email == u.email)
    )).scalars().all()
    assert len(failed) == 1


async def test_mfa_flow_with_real_redis(client, test_engine):
    """MFA login: password → mfa_token, OTP read straight from Redis, challenge → tokens."""
    import redis.asyncio as aioredis
    from app.core.config import settings

    u = await make_user(test_engine, mfa_enabled=True)
    r = await client.post("/identity/v1/auth/login", json={"email": u.email, "password": PWD})
    assert r.status_code == 200
    body = r.json()
    assert body.get("mfa_required") is True

    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    otp = await redis.get(otp_redis_key(str(u.id)))
    assert otp is not None

    r2 = await client.post("/identity/v1/auth/mfa/challenge",
                           json={"mfa_token": body["mfa_token"], "code": otp})
    assert r2.status_code == 200, r2.text
    assert "access_token" in r2.json()

    # OTP is single-use
    r3 = await client.post("/identity/v1/auth/mfa/challenge",
                           json={"mfa_token": body["mfa_token"], "code": otp})
    assert r3.status_code == 401
    await redis.aclose()


async def test_refresh_rotates_and_blacklists(client, test_engine):
    u = await make_user(test_engine)
    login = (await client.post("/identity/v1/auth/login",
                               json={"email": u.email, "password": PWD})).json()
    rt = login["refresh_token"]

    r1 = await client.post("/identity/v1/auth/refresh", json={"refresh_token": rt})
    assert r1.status_code == 200
    # reuse of the consumed refresh token is rejected
    r2 = await client.post("/identity/v1/auth/refresh", json={"refresh_token": rt})
    assert r2.status_code == 401


async def test_logout_blacklists_refresh(client, test_engine):
    u = await make_user(test_engine)
    login = (await client.post("/identity/v1/auth/login",
                               json={"email": u.email, "password": PWD})).json()
    rt = login["refresh_token"]
    r = await client.post("/identity/v1/auth/logout", json={"refresh_token": rt})
    assert r.status_code == 204
    r2 = await client.post("/identity/v1/auth/refresh", json={"refresh_token": rt})
    assert r2.status_code == 401


async def test_me_roundtrip(auth_client):
    r = await auth_client.get("/identity/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == auth_client.test_user.email

    r2 = await auth_client.patch("/identity/v1/auth/me", json={"full_name": "Renamed"})
    assert r2.status_code == 200
    assert r2.json()["full_name"] == "Renamed"


async def test_change_password_and_audit(client, test_engine, db_session):
    u = await make_user(test_engine)
    login = (await client.post("/identity/v1/auth/login",
                               json={"email": u.email, "password": PWD})).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    r = await client.post("/identity/v1/auth/change-password", headers=headers,
                          json={"current_password": "nope-wrong", "new_password": "NewPass123!"})
    assert r.status_code == 400

    r = await client.post("/identity/v1/auth/change-password", headers=headers,
                          json={"current_password": PWD, "new_password": "NewPass123!"})
    assert r.status_code == 204
    assert "password_changed" in await _audit_actions(db_session, u.id)

    r2 = await client.post("/identity/v1/auth/login",
                           json={"email": u.email, "password": "NewPass123!"})
    assert r2.status_code == 200


async def test_register_conflict(client, test_engine):
    u = await make_user(test_engine)
    r = await client.post("/identity/v1/auth/register", json={
        "email": u.email, "password": "Whatever123!", "full_name": "Dup",
    })
    assert r.status_code == 409
