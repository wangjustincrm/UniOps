"""I4 (final-phase review): /boms/effective, /boms/explode and
/boms/where-used were authentication-only (any logged-in role, via
CurrentUser) even though a BOM is a trade secret — the full formulation of
every finished product, reverse-mappable via /where-used. They must be
gated `mrp.report.view` like /sync-state already was.

mdm-api's `client` fixture (see tests/conftest.py) always overrides the
token payload to system_admin, which bypasses every permission gate — so an
HTTP round trip alone can never observe a 403 here. This suite uses the same
two-pronged approach test_authz_any_permission.py established:

  1. A direct unit test of `require_permission("mrp.report.view")` — the
     exact dependency callable boms.py's `ReportViewDep` wraps — with
     `uniops_authz.core.user_role_codes`/`_effective_matrix` monkeypatched
     so a role that does NOT hold the key gets a real 403, without needing
     identity's role_permissions/user_roles tables to exist in this
     service's own test DB (they don't — mdm-api's alembic chain doesn't
     own them).
  2. An HTTP-level wiring test per endpoint, re-overriding get_token_payload
     to a non-admin role (after the `client` fixture's default admin
     override) with the same monkeypatch, proving the dependency is
     actually wired onto each route — not just defined and unused.
"""
import uuid

import pytest

from app.core.authz import require_permission


class _FakeDB:
    """Never touched — user_role_codes/_effective_matrix are monkeypatched
    in every test here, so the gate never issues real SQL."""


def _deny_everything(monkeypatch):
    """Make every role's effective permission set empty, so any key lookup
    (other than the system_admin short-circuit) falls through to 403."""
    import uniops_authz.core as authz_core

    async def _user_role_codes(db, user_id, base_role):
        return {base_role}

    async def _effective_matrix(db):
        return {}

    monkeypatch.setattr(authz_core, "user_role_codes", _user_role_codes)
    monkeypatch.setattr(authz_core, "_effective_matrix", _effective_matrix)


@pytest.mark.anyio
async def test_report_view_gate_403s_a_role_without_the_key(monkeypatch):
    from fastapi import HTTPException

    _deny_everything(monkeypatch)

    check = require_permission("mrp.report.view")
    payload = {"sub": str(uuid.uuid4()), "role": "vendor_manager"}
    with pytest.raises(HTTPException) as exc_info:
        await check(payload=payload, db=_FakeDB())
    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_report_view_gate_admits_a_role_that_holds_the_key(monkeypatch):
    import uniops_authz.core as authz_core

    async def _user_role_codes(db, user_id, base_role):
        return {base_role}

    async def _effective_matrix(db):
        return {"planner": {"mrp.report.view"}}

    monkeypatch.setattr(authz_core, "user_role_codes", _user_role_codes)
    monkeypatch.setattr(authz_core, "_effective_matrix", _effective_matrix)

    check = require_permission("mrp.report.view")
    payload = {"sub": str(uuid.uuid4()), "role": "planner"}
    result = await check(payload=payload, db=_FakeDB())
    assert result is payload


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path,params",
    [
        ("/mdm/v1/boms/effective", {"product": "S0093", "date": "2026-08-04"}),
        ("/mdm/v1/boms/explode", {"product": "S0093", "date": "2026-08-04"}),
        ("/mdm/v1/boms/where-used", {"component": "CR0031", "date": "2026-08-04"}),
    ],
)
async def test_endpoint_403s_a_non_permitted_role(client, db_session, monkeypatch, path, params):
    """Endpoint-wiring proof: a role that is authenticated but does NOT hold
    mrp.report.view gets refused at each of the three routes — before this
    fix, all three admitted any authenticated role regardless of key."""
    from app.core.deps import get_token_payload
    from app.main import app

    _deny_everything(monkeypatch)

    async def _override_non_admin_user():
        return {"sub": str(uuid.uuid4()), "role": "vendor_manager", "type": "access"}

    app.dependency_overrides[get_token_payload] = _override_non_admin_user
    try:
        resp = await client.get(path, params=params)
    finally:
        # Restore the client fixture's default system_admin override so
        # this test doesn't leak state into whatever runs after it.
        async def _override_admin_user():
            return {"sub": str(uuid.uuid4()), "role": "system_admin", "type": "access"}

        app.dependency_overrides[get_token_payload] = _override_admin_user

    assert resp.status_code == 403, resp.text
