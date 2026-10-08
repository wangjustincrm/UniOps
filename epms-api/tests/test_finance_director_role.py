"""finance_director registration in epms-api — the post that gives the final
payment sign-off after Finance Manager (identity migration 0015 seeds it,
approval-api routes the step).

Handled like payment_officer: an ADDITIONAL role, so it is in the built-in
fallback constants but never in VALID_ROLES (the primary-login-role list).
"""
import httpx
import pytest

from app.api.v1 import config as config_api
from app.core.access_scope import _POST_CODES, _RESTRICTED_ROLES
from app.crud.current_step import ROLE_ORDER, role_label


@pytest.mark.asyncio
async def test_listed_with_label_when_identity_down(admin_client, mocker):
    mocker.patch.object(
        config_api, "_forward_identity",
        mocker.AsyncMock(side_effect=httpx.ConnectError("identity is down")),
    )
    r = await admin_client.get("/api/v1/config/roles")
    assert r.status_code == 200
    roles = {row["code"]: row for row in r.json()}
    assert roles["finance_director"]["name"] == "Finance Director"
    assert roles["finance_director"]["assignable_as_primary"] is False


def test_is_not_a_primary_login_role():
    from app.schemas.user import VALID_ROLES
    assert "finance_director" not in VALID_ROLES


def test_holder_can_open_any_pa():
    """The approve task is broadcast by role; the holder must be able to open
    a PA from any department, so the role must not be scope-restricted."""
    assert "finance_director" in _POST_CODES
    assert "finance_director" not in _RESTRICTED_ROLES


def test_current_step_label_and_order():
    assert role_label("finance_director") == "Finance Director"
    assert ROLE_ORDER["finance_manager"] < ROLE_ORDER["finance_director"] < ROLE_ORDER["vendor_manager"]
