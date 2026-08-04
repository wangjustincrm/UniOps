"""require_any_permission() unit tests.

mdm-api's `client` fixture (see tests/conftest.py) always overrides the
token payload to system_admin, which short-circuits every gate in this
service regardless of key — that's deliberate (exercises endpoint wiring
without needing identity's role/permission tables, which mdm-api's own
alembic chain doesn't own). It means an HTTP-level test can never observe
the OR-of-keys behavior itself, so this suite calls the dependency function
directly and monkeypatches `has_permission` (the uniops_authz primitive it's
built on) to control each key's outcome.
"""
import uuid

import pytest

from app.core.authz import require_any_permission


class _FakeDB:
    """Never touched — has_permission is monkeypatched in every test here."""


@pytest.mark.anyio
async def test_system_admin_bypasses_without_querying_permissions(monkeypatch):
    import app.core.authz as authz_module

    async def _boom(*a, **kw):
        raise AssertionError("has_permission must not be called for system_admin")

    monkeypatch.setattr(authz_module, "has_permission", _boom)

    check = require_any_permission("data_maintenance", "mdm.bom.write")
    payload = {"sub": str(uuid.uuid4()), "role": "system_admin"}
    result = await check(payload=payload, db=_FakeDB())
    assert result is payload


@pytest.mark.anyio
async def test_grants_when_either_key_is_held(monkeypatch):
    import app.core.authz as authz_module

    async def _has(db, uid, role, key):
        return key == "mdm.bom.write"  # only the narrow key is granted

    monkeypatch.setattr(authz_module, "has_permission", _has)

    check = require_any_permission("data_maintenance", "mdm.bom.write")
    payload = {"sub": str(uuid.uuid4()), "role": "requester"}
    result = await check(payload=payload, db=_FakeDB())
    assert result is payload


@pytest.mark.anyio
async def test_grants_via_the_broad_data_maintenance_key_too(monkeypatch):
    """Existing data_maintenance admins must keep working unchanged even
    though they were never granted the new mdm.bom.write key."""
    import app.core.authz as authz_module

    async def _has(db, uid, role, key):
        return key == "data_maintenance"

    monkeypatch.setattr(authz_module, "has_permission", _has)

    check = require_any_permission("data_maintenance", "mdm.bom.write")
    payload = {"sub": str(uuid.uuid4()), "role": "vendor_manager"}
    result = await check(payload=payload, db=_FakeDB())
    assert result is payload


@pytest.mark.anyio
async def test_403_when_neither_key_is_held(monkeypatch):
    from fastapi import HTTPException

    import app.core.authz as authz_module

    async def _has(db, uid, role, key):
        return False

    monkeypatch.setattr(authz_module, "has_permission", _has)

    check = require_any_permission("data_maintenance", "mdm.bom.write")
    payload = {"sub": str(uuid.uuid4()), "role": "requester"}
    with pytest.raises(HTTPException) as exc_info:
        await check(payload=payload, db=_FakeDB())
    assert exc_info.value.status_code == 403
