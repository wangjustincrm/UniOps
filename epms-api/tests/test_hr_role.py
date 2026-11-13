"""hr registration in epms-api — additional-only, many holders, no EPMS grants."""
from app.core.access_scope import _POST_CODES
from app.crud.config import (
    _BUILTIN_ROLE_NAMES,
    _DEFAULT_ROLE_PERMISSIONS,
    ADDITIONAL_ONLY_ROLES,
    BUILT_IN_ROLES,
)


def test_hr_is_a_built_in_additional_only_role():
    assert "hr" in BUILT_IN_ROLES
    assert "hr" in ADDITIONAL_ONLY_ROLES
    assert _BUILTIN_ROLE_NAMES["hr"] == "HR"


def test_hr_is_not_a_post():
    assert "hr" not in _POST_CODES


def test_hr_holds_no_epms_permission():
    assert not any(_DEFAULT_ROLE_PERMISSIONS["hr"].values())
