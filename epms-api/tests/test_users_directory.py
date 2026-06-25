"""Regression: GET /api/v1/users/directory is open to any authenticated role.

This contract is consumed by vms-api Host search (PRD §6.2). Closing it down
to system_admin (as the legacy /users endpoint is) would break VMS.
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.schemas.auth import RegisterRequest


async def _make_user(test_engine, role: str) -> tuple[str, str]:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(
            db,
            RegisterRequest(
                email=f"{role}-{uuid.uuid4().hex[:6]}@directory-test.com",
                password="TestPass1!",
                full_name=f"Directory {role.title()}",
                role=role,
            ),
        )
        await db.commit()
    token = create_access_token(str(user.id), user.role)
    return str(user.id), token


def _authed_client(token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


# ── Open-access matrix ───────────────────────────────────────────────────────

@pytest.mark.parametrize("role", [
    "requester",
    "dept_manager",
    "auditor",
    "gm",
    "opm",
    "finance_bp",
    "system_admin",
])
@pytest.mark.asyncio
async def test_directory_endpoint_open_to_all_roles(test_engine, role):
    """Every UniOps role should get 200 from /users/directory."""
    _, token = await _make_user(test_engine, role)
    async with _authed_client(token) as c:
        resp = await c.get("/api/v1/users/directory")
    assert resp.status_code == 200, (
        f"role={role} got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "items" in body
    assert "total" in body
    # Caller themselves should appear in the directory (they're active).
    assert body["total"] >= 1


@pytest.mark.asyncio
async def test_directory_rejects_anonymous(test_engine):
    """No token → 401 / 403 (not 200)."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/users/directory")
    assert resp.status_code in (401, 403), resp.text


# ── Search + pagination behavior ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_directory_search_filters_by_name(test_engine):
    _, token_a = await _make_user(test_engine, "requester")
    # Create a second user with a distinctive name we can search for.
    unique_marker = f"zzdirsearch{uuid.uuid4().hex[:6]}"
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await user_crud.create(
            db,
            RegisterRequest(
                email=f"{unique_marker}@directory-test.com",
                password="TestPass1!",
                full_name=f"Searchable {unique_marker}",
                role="requester",
            ),
        )
        await db.commit()
    async with _authed_client(token_a) as c:
        resp = await c.get(f"/api/v1/users/directory?search={unique_marker}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert unique_marker in body["items"][0]["full_name"]


# ── Single-user endpoint ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_directory_single_lookup_open_to_requester(test_engine):
    target_uid, _ = await _make_user(test_engine, "dept_manager")
    _, caller_token = await _make_user(test_engine, "requester")
    async with _authed_client(caller_token) as c:
        resp = await c.get(f"/api/v1/users/directory/{target_uid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == target_uid
    # Brief response should NOT leak admin-only fields.
    assert "role" not in body
    assert "is_active" not in body
    assert "mfa_enabled" not in body
    assert "hashed_password" not in body


@pytest.mark.asyncio
async def test_directory_single_lookup_404_on_missing_user(test_engine):
    _, caller_token = await _make_user(test_engine, "requester")
    async with _authed_client(caller_token) as c:
        resp = await c.get(f"/api/v1/users/directory/{uuid.uuid4()}")
    assert resp.status_code == 404
