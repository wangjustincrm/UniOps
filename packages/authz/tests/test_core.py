"""The gate's semantics, pinned. These are the rules every service inherits."""
import uuid

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from uniops_authz import bind, effective_permissions, user_role_codes

pytestmark = pytest.mark.asyncio


def _noop_dep():
    """Stand-in get_db_dep/get_user_dep for bind() tests below — the checker
    coroutine is invoked directly with explicit kwargs, bypassing FastAPI's
    Depends() resolution, so these are never actually called."""
    raise AssertionError("should not be invoked directly")


async def _mk_user(db, role: str) -> uuid.UUID:
    uid = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role, is_active) "
        "VALUES (:i, :e, 'x', 'U', :r, true)"),
        {"i": str(uid), "e": f"{uid}@t.co", "r": role})
    return uid


async def test_role_codes_union_primary_and_additional(authz_db):
    """A post/role held as the PRIMARY role counts, same as an ADDITIONAL one.
    Consulting only user_roles would silently lose primary-role holders."""
    db = authz_db
    uid = await _mk_user(db, "dept_manager")
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_bp')"),
        {"u": str(uid)})
    codes = await user_role_codes(db, uid, "dept_manager")
    assert codes == {"dept_manager", "finance_bp"}


async def test_permission_granted_via_any_role(authz_db):
    db = authz_db
    uid = await _mk_user(db, "requester")
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'ap_clerk')"),
        {"u": str(uid)})
    await db.execute(sa.text(
        "INSERT INTO role_permissions (role_code, permission_key) VALUES ('ap_clerk', 'k.write')"))
    perms = await effective_permissions(db, uid, "requester")
    assert perms.get("k.write") is True


async def test_locked_cell_counts_as_granted(authz_db):
    """The effective matrix is granted UNION locked — a lock is a forced grant."""
    db = authz_db
    uid = await _mk_user(db, "requester")
    await db.execute(sa.text(
        "INSERT INTO role_permission_locks (role_code, permission_key) "
        "VALUES ('requester', 'k.locked')"))
    perms = await effective_permissions(db, uid, "requester")
    assert perms.get("k.locked") is True


async def test_ungranted_key_is_false_not_missing(authz_db):
    db = authz_db
    uid = await _mk_user(db, "requester")
    await db.execute(sa.text(
        "INSERT INTO permission_defs (key, module, label, sort) "
        "VALUES ('k.nope', 'test', 'Nope', 1)"))
    perms = await effective_permissions(db, uid, "requester")
    assert perms.get("k.nope") is False


async def test_inactive_additional_role_ignored(authz_db):
    """A role_defs row with is_active=false must not contribute permissions."""
    db = authz_db
    uid = await _mk_user(db, "requester")
    await db.execute(sa.text(
        "INSERT INTO role_defs (code, label, sort, is_active) "
        "VALUES ('retired_role', 'Retired', 99, false) ON CONFLICT DO NOTHING"))
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'retired_role')"),
        {"u": str(uid)})
    await db.execute(sa.text(
        "INSERT INTO role_permissions (role_code, permission_key) "
        "VALUES ('retired_role', 'k.retired')"))
    # Register the key itself, same as test_ungranted_key_is_false_not_missing
    # does for 'k.nope'. effective_permissions() only returns keys sourced
    # from permission_defs — without this row 'k.retired' is simply absent
    # from the result dict, and `.get(...) is not True` degenerates to
    # `None is not True` (vacuously true regardless of whether the
    # is_active filter on the JOIN actually works).
    await db.execute(sa.text(
        "INSERT INTO permission_defs (key, module, label, sort) "
        "VALUES ('k.retired', 'test', 'Retired', 1)"))
    perms = await effective_permissions(db, uid, "requester")
    assert perms.get("k.retired") is False


async def test_bind_system_admin_short_circuits(authz_db):
    """Every require_roles() helper being replaced starts with
    `if role == "system_admin": return payload` — bind() must preserve that
    exactly, without even touching the DB for a role/permission lookup."""
    require_permission = bind(_noop_dep, _noop_dep)
    check = require_permission("anything.at.all")
    payload = {"role": "system_admin", "sub": str(uuid.uuid4())}
    result = await check(payload=payload, db=authz_db)
    assert result is payload


async def test_bind_no_permission_raises_403(authz_db):
    db = authz_db
    uid = await _mk_user(db, "requester")
    require_permission = bind(_noop_dep, _noop_dep)
    check = require_permission("k.missing")
    payload = {"role": "requester", "sub": str(uid)}
    with pytest.raises(HTTPException) as exc_info:
        await check(payload=payload, db=db)
    assert exc_info.value.status_code == 403


async def test_bind_permits_when_role_has_permission(authz_db):
    db = authz_db
    uid = await _mk_user(db, "requester")
    await db.execute(sa.text(
        "INSERT INTO role_permissions (role_code, permission_key) "
        "VALUES ('requester', 'k.allowed')"))
    require_permission = bind(_noop_dep, _noop_dep)
    check = require_permission("k.allowed")
    payload = {"role": "requester", "sub": str(uid)}
    result = await check(payload=payload, db=db)
    assert result is payload
