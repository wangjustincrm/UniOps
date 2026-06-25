"""Guard: every built-in app role must be able to authenticate to mdm-api.

mdm-api is a master-data service that all roles must be able to READ (units,
departments, parts, …). The ALLOWED_ROLES gate is authentication-only; writes
are restricted per-endpoint via require_roles(...). Regression test for the
bug where procurement_officer (PO creator) got 403 and UOM dropdowns silently
fell back to the hardcoded unit list.
"""
from app.core.deps import ALLOWED_ROLES

# Built-in roles defined in epms-api (app/crud/config.py BUILT_IN_ROLES).
BUILT_IN_APP_ROLES = {
    "requester", "dept_admin", "dept_manager", "gm", "opm",
    "procurement_officer", "procurement_manager", "warehouse_staff",
    "ap_clerk", "finance_bp", "finance_manager", "vendor_manager",
    "cfo", "auditor", "system_admin",
}


def test_all_builtin_roles_can_authenticate_to_mdm():
    missing = BUILT_IN_APP_ROLES - ALLOWED_ROLES
    assert not missing, f"roles missing from mdm ALLOWED_ROLES (will get 403): {missing}"
