"""Guard: booking-api's authz gates resolve against uniops_authz (identity's
shared role_permissions / role_permission_locks tables) — NOT company_config.

Phase 2 deleted the company_config write-through that used to keep booking's
local JSONB mirror in sync with identity's matrix; booking-api now reads the
same tables every other migrated service reads (see app/core/authz.py,
app/core/permissions.py). Tables are shadowed and seeded in conftest.py's
test_engine fixture: view_booking is granted to every built-in role
(matching identity's seed_authz.py DEFAULTS), manage_meeting_rooms is
matrix-only — grant it per-test via grant_matrix_permission().
"""
import uuid

import pytest
from uniops_authz import has_permission

from app.core.permissions import is_booking_admin
from tests.conftest import grant_matrix_permission


async def test_system_admin_always_allowed_even_ungranted(db_session):
    assert await has_permission(db_session, uuid.uuid4(), "system_admin", "manage_meeting_rooms") is True


async def test_requester_has_view_booking_from_seeded_default(db_session):
    assert await has_permission(db_session, uuid.uuid4(), "requester", "view_booking") is True


async def test_requester_lacks_manage_meeting_rooms_by_default(db_session):
    payload = {"sub": str(uuid.uuid4()), "role": "requester"}
    assert await is_booking_admin(payload, db_session) is False


async def test_matrix_grant_gives_manage_meeting_rooms_to_procurement_manager(db_session):
    """A non-system_admin role granted manage_meeting_rooms via the matrix
    (e.g. procurement_manager) is treated as a booking admin — the union-role
    resolution app/core/permissions.py's docstring describes."""
    await grant_matrix_permission(db_session, "procurement_manager", "manage_meeting_rooms")
    payload = {"sub": str(uuid.uuid4()), "role": "procurement_manager"}
    assert await is_booking_admin(payload, db_session) is True


async def test_additional_role_also_grants_manage_meeting_rooms(db_session):
    """Role union (primary ∪ additional user_roles) — a requester with an
    ADDITIONAL procurement_manager role assignment also gets admin power.
    This is the intended broadening from the old PRIMARY-role-only gate."""
    import sqlalchemy as sa

    await grant_matrix_permission(db_session, "procurement_manager", "manage_meeting_rooms")
    user_id = uuid.uuid4()
    await db_session.execute(
        sa.text("INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'procurement_manager')"),
        {"u": str(user_id)},
    )
    await db_session.flush()
    payload = {"sub": str(user_id), "role": "requester"}
    assert await is_booking_admin(payload, db_session) is True
