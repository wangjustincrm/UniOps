from app.core.budget_scope import FULL_ACCESS_PRIMARY, FULL_ACCESS_ASSIGNED


def test_role_sets_match_budget_api():
    # Pins the duplicated constants so finance-api and budget-api cannot drift.
    assert FULL_ACCESS_PRIMARY == {
        "gm", "opm", "finance_manager", "ap_clerk",
        "system_admin", "cfo", "auditor", "procurement_manager",
    }
    assert FULL_ACCESS_ASSIGNED == {
        "gm", "opm", "finance_manager", "procurement_manager", "finance_bp",
    }
