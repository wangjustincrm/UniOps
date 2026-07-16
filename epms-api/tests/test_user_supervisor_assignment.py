"""Regression: PATCH /api/v1/users/{id} accepts and persists supervisor_id.

Tests the full write/read path for users.supervisor_id:
  1. Create two admin users (subject + supervisor).
  2. PATCH subject's supervisor_id → supervisor's id.
  3. GET subject → assert supervisor_id is returned.
  4. PATCH supervisor_id → null → assert it is cleared.
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.schemas.auth import RegisterRequest


async def _make_user(test_engine, role: str = "requester") -> tuple[str, str]:
    """Create a user in the test DB; return (user_id_str, token)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(
            db,
            RegisterRequest(
                email=f"{role}-{uuid.uuid4().hex[:8]}@supervisor-test.com",
                password="TestPass1!",
                full_name=f"Supervisor Test {role.title()}",
                role=role,
            ),
        )
        await db.commit()
    token = create_access_token(str(user.id), user.role)
    return str(user.id), token


@pytest.mark.asyncio
async def test_patch_supervisor_id_set_and_clear(test_engine, admin_client):
    """PATCH sets supervisor_id; GET returns it; PATCH null clears it."""
    # --- arrange: create a second user to act as supervisor ---
    supervisor_id, _ = await _make_user(test_engine, "dept_manager")
    subject_id, _ = await _make_user(test_engine, "requester")

    # --- act: set supervisor_id ---
    resp = await admin_client.patch(
        f"/api/v1/users/{subject_id}",
        json={"supervisor_id": supervisor_id},
    )
    assert resp.status_code == 200, f"PATCH set failed: {resp.text}"
    data = resp.json()
    assert data["supervisor_id"] == supervisor_id, (
        f"Expected supervisor_id={supervisor_id}, got {data.get('supervisor_id')}"
    )

    # --- verify: GET returns the same value ---
    get_resp = await admin_client.get(f"/api/v1/users/{subject_id}")
    assert get_resp.status_code == 200, f"GET failed: {get_resp.text}"
    get_data = get_resp.json()
    assert get_data["supervisor_id"] == supervisor_id, (
        f"GET: expected supervisor_id={supervisor_id}, got {get_data.get('supervisor_id')}"
    )

    # --- act: clear supervisor_id by sending null ---
    clear_resp = await admin_client.patch(
        f"/api/v1/users/{subject_id}",
        json={"supervisor_id": None},
    )
    assert clear_resp.status_code == 200, f"PATCH clear failed: {clear_resp.text}"
    clear_data = clear_resp.json()
    assert clear_data["supervisor_id"] is None, (
        f"Expected supervisor_id=null after clear, got {clear_data.get('supervisor_id')}"
    )


@pytest.mark.asyncio
async def test_patch_omit_supervisor_id_leaves_unchanged(test_engine, admin_client):
    """Omitting supervisor_id in PATCH body does not clear it."""
    supervisor_id, _ = await _make_user(test_engine, "dept_manager")
    subject_id, _ = await _make_user(test_engine, "requester")

    # First set it.
    set_resp = await admin_client.patch(
        f"/api/v1/users/{subject_id}",
        json={"supervisor_id": supervisor_id},
    )
    assert set_resp.status_code == 200, set_resp.text

    # Now PATCH something else entirely (full_name); supervisor_id must be preserved.
    update_resp = await admin_client.patch(
        f"/api/v1/users/{subject_id}",
        json={"full_name": "Updated Name"},
    )
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["supervisor_id"] == supervisor_id, (
        "supervisor_id was cleared by an unrelated PATCH that omitted it"
    )


@pytest.mark.asyncio
async def test_patch_role_to_held_singleton_post_rejected_409(test_engine, admin_client):
    """gm/opm/vendor_manager/finance_manager/procurement_manager are
    company-unique posts (identity enforces one holder). The still-live EPMS
    Users admin must not be able to arm a second holder via PATCH .../role —
    that desync (users.role UNION user_roles both resolving in
    approval-api's _post_holders) makes routing nondeterministic."""
    _existing_gm_id, _ = await _make_user(test_engine, "gm")
    subject_id, _ = await _make_user(test_engine, "requester")

    resp = await admin_client.patch(
        f"/api/v1/users/{subject_id}",
        json={"role": "gm"},
    )
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_patch_role_resave_current_holder_is_idempotent(test_engine, admin_client):
    """Re-saving the current holder's own post role must NOT 409 — the
    conflict check excludes the user being edited."""
    holder_id, _ = await _make_user(test_engine, "opm")

    resp = await admin_client.patch(
        f"/api/v1/users/{holder_id}",
        json={"role": "opm"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_patch_role_to_finance_bp_allows_multiple_holders(test_engine, admin_client):
    """finance_bp is a job FUNCTION, not a singleton post — multiple
    concurrent holders (as PRIMARY role) must remain allowed."""
    await _make_user(test_engine, "finance_bp")
    subject_id, _ = await _make_user(test_engine, "requester")

    resp = await admin_client.patch(
        f"/api/v1/users/{subject_id}",
        json={"role": "finance_bp"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_create_user_with_held_singleton_post_rejected_409(test_engine, admin_client):
    """POST /users must apply the same singleton-post guard as PATCH."""
    await _make_user(test_engine, "vendor_manager")

    resp = await admin_client.post(
        "/api/v1/users",
        json={
            "email": f"new-{uuid.uuid4().hex[:8]}@singleton-test.com",
            "full_name": "New Vendor Manager",
            "role": "vendor_manager",
            "password": "TestPass1!",
            "erp_person_code": f"ERP-{uuid.uuid4().hex[:8]}",
        },
    )
    assert resp.status_code == 409, resp.text
