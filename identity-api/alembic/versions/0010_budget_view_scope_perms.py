"""Budget Dashboard data scope becomes matrix-driven.

Registers the two keys that replace the hardcoded role lists that used to live
in budget-api/finance-api's app/core/budget_scope.py:

    finance.budget.view_all   -> company-wide budget numbers
    finance.budget.view_dept  -> own department + directed departments + (for
                                 the OPM) departments routed to the OPM

These are DATA-SCOPE keys, orthogonal to view_budget_dashboard, which decides
whether the page/nav entry is reachable at all.

Seeding the grants here rather than leaving them for an admin to tick is
deliberate: budget_scope.py fails CLOSED, so a service rolled out against a DB
without these grants would show everyone an empty dashboard (the same shape as
the Create-GR cutover that 403'd everyone — see 0006_agreement_perms).

Defaults, per the 2026-08-17 decision:
  * view_all  -> the finance/audit posts that are company-wide by nature.
  * view_dept -> EVERY other role, which reproduces today's behaviour exactly
    (a non-full-access caller has always been scoped to their department).
    Granting it widely is safe: it only ever exposes the caller's OWN
    departments, and reaching the page still needs view_budget_dashboard.
  * Roles losing company-wide access here: opm, procurement_manager. Both were
    on the old hardcoded list; the OPM keeps every department routed to them
    via approval_dept_routing.gm_or_opm.

Idempotent (ON CONFLICT DO NOTHING); safe on a DB the seed scripts already
touched. On a virgin DB, where role_defs is still empty when migrations run,
the two grant statements match no rows and seed_phase2_keys.py supplies the
same defaults afterwards.
"""
from alembic import op

revision = "0010_budget_view_scope"
down_revision = "0009_role_assignable_as_primary"
branch_labels = None
depends_on = None

# sort follows the phase-2 convention (100+, finance block at 110+), so the two
# rows land in the FINANCE group next to the other phase-2 finance keys in
# Portal Admin -> Access Control. Kept in sync with seed_phase2_keys.PHASE2_KEYS.
_KEYS = {
    "finance.budget.view_all":  ("finance", "Full Access Budget View", 113),
    "finance.budget.view_dept": ("finance", "Department-Related Budget View", 114),
}

_VIEW_ALL_ROLES = (
    "gm", "finance_manager", "ap_clerk", "system_admin",
    "finance_bp", "auditor", "cfo",
)


def upgrade() -> None:
    for key, (module, label, sort) in _KEYS.items():
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','{module}','{label}',{sort}) ON CONFLICT (key) DO NOTHING")

    roles = "','".join(_VIEW_ALL_ROLES)
    op.execute(
        "INSERT INTO role_permissions(role_code,permission_key) "
        "SELECT code, 'finance.budget.view_all' FROM role_defs "
        f"WHERE code IN ('{roles}') ON CONFLICT DO NOTHING")
    # Everything else — including custom roles an admin created — keeps exactly
    # the department scope it has today.
    op.execute(
        "INSERT INTO role_permissions(role_code,permission_key) "
        "SELECT code, 'finance.budget.view_dept' FROM role_defs "
        f"WHERE code NOT IN ('{roles}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Only the grants + defs this migration added. Do NOT touch role_defs —
    # every role referenced here pre-exists.
    for key in _KEYS:
        op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{key}'")
        op.execute(f"DELETE FROM permission_defs WHERE key = '{key}'")
