"""payment_officer additional-role registration in epms-api's Access Control
Matrix (Task 2 of the payment_officer rollout — Task 1 seeded the role +
its 3 grants in identity's migration 0008).

payment_officer is granted per-user via identity's user_roles, never as a
primary login role, so app.schemas.user.VALID_ROLES must never contain it
(see the guard test below — it mirrors how erp_pa_officer is handled: in
BUILT_IN_ROLES/LOCKED_PERMISSIONS/_ROLE_DEFAULTS/_BUILTIN_ROLE_NAMES, absent
from VALID_ROLES).

GET /config/roles and GET /config/locked-permissions proxy identity's
/authz/defs and only fall back to the local BUILT_IN_ROLES /
_BUILTIN_ROLE_NAMES / LOCKED_PERMISSIONS constants when identity is
unreachable (httpx.RequestError) — see app/api/v1/config.py. Mocking
_forward_identity to raise is the standard way this suite exercises that
fallback path (see tests/test_authz_proxy.py).
"""
import httpx
import pytest

from app.api.v1 import config as config_api


@pytest.mark.asyncio
async def test_payment_officer_listed_with_label_when_identity_down(admin_client, mocker):
    mocker.patch.object(
        config_api, "_forward_identity",
        mocker.AsyncMock(side_effect=httpx.ConnectError("identity is down")),
    )
    r = await admin_client.get("/api/v1/config/roles")
    assert r.status_code == 200
    roles = {row["code"]: row for row in r.json()}
    assert "payment_officer" in roles
    assert roles["payment_officer"]["name"] == "Payment Officer"
    assert roles["payment_officer"]["is_builtin"] is True


@pytest.mark.asyncio
async def test_payment_officer_view_pa_locked_when_identity_down(admin_client, mocker):
    mocker.patch.object(
        config_api, "_forward_identity",
        mocker.AsyncMock(side_effect=httpx.ConnectError("identity is down")),
    )
    r = await admin_client.get("/api/v1/config/locked-permissions")
    assert r.status_code == 200
    assert "view_pa" in r.json()["payment_officer"]


def test_payment_officer_is_not_a_primary_login_role():
    from app.schemas.user import VALID_ROLES
    assert "payment_officer" not in VALID_ROLES, (
        "payment_officer 是附加角色。VALID_ROLES 校验的是主登录角色 —— "
        "加进去会让它可被设为某人的主角色,复制 erp_pa_officer 的处理方式")
