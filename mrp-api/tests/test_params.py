"""Planning parameters (key-value settings) — GET/PUT over `mrp_planning_params`.

`week_calendar_mode` is the only writable key as of Task 2 (Phase 1C's
loss-rate parameters land in the same table later — see
app/models/params.py's docstring). Round-trip + default + unknown-mode
rejection + the write-permission gate.
"""
import pytest


def _deny_everything(monkeypatch):
    """Copied from tests/test_permission_gates.py's helper of the same name
    (brief explicitly allows import-or-copy) — this DB has none of
    identity's role_permissions/role_defs/user_roles tables, so a real
    require_permission(...) gate hit with `non_admin_token` would 500 on a
    missing table rather than 403 without this monkeypatch. `admin_token`
    always short-circuits via the system_admin fast path and would prove
    nothing about the actual permission key."""
    import uniops_authz.core as authz_core

    async def _user_role_codes(db, user_id, base_role):
        return {base_role}

    async def _effective_matrix(db):
        return {}

    monkeypatch.setattr(authz_core, "user_role_codes", _user_role_codes)
    monkeypatch.setattr(authz_core, "_effective_matrix", _effective_matrix)


@pytest.mark.asyncio
async def test_week_calendar_mode_defaults_to_iso_thursday(client, auth_headers):
    r = await client.get("/api/v1/params", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["week_calendar_mode"] == "iso_thursday"


@pytest.mark.asyncio
async def test_week_calendar_mode_round_trips(client, auth_headers):
    r = await client.put("/api/v1/params/week_calendar_mode",
                         json={"value": "month_fixed"}, headers=auth_headers)
    assert r.status_code == 200
    assert (await client.get("/api/v1/params",
                             headers=auth_headers)).json()["week_calendar_mode"] == "month_fixed"


@pytest.mark.asyncio
async def test_unknown_week_mode_is_rejected(client, auth_headers):
    r = await client.put("/api/v1/params/week_calendar_mode",
                         json={"value": "fiscal_445"}, headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_params_write_requires_param_write_permission(client, non_admin_token, monkeypatch):
    # non_admin_token + _deny_everything, not admin_token — see module
    # docstring / helper docstring above.
    _deny_everything(monkeypatch)
    r = await client.put("/api/v1/params/week_calendar_mode", json={"value": "month_fixed"},
                         headers={"Authorization": f"Bearer {non_admin_token}"})
    assert r.status_code == 403
