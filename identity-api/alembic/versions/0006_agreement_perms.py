"""Register + grant the Purchase Agreement permission keys.

Phase-2 style keys (permission_defs + role_permissions) rather than a phase-1
matrix key: phase-1 keys live in seed_authz.MODULE_BY_KEY/DEFAULTS/LOCKED, are
re-derived by verify_gate_parity.py, and are hand-copied into epms-api's
conftest — four files that must stay in sync. Phase-2 needs only this migration
and seed_phase2_keys.py.

Seeding the grants here rather than leaving them for an admin to tick is
deliberate: the Create-GR cutover shipped a matrix-driven gate with no seeded
grant and 403'd everyone who previously had the button.

Idempotent (ON CONFLICT DO NOTHING); safe on a DB the seed scripts already
touched and self-sufficient on a fresh one.

Revision id is 20 chars — alembic_version_identity.version_num is varchar(32).
"""
from alembic import op

revision = "0006_agreement_perms"
down_revision = "0005_procurement_officer_pa"
branch_labels = None
depends_on = None

_KEYS = {
    "epms.agreement.read":  ("epms", "View Agreements", 104),
    "epms.agreement.write": ("epms", "Create / Edit Agreements", 105),
}

# Read: everyone already in the procurement/AP chain, PLUS every role an admin
# can put in the `agr` approval chain, PLUS requester.
#
# The approval roles are not optional. The seeded default `agr` chain is
# dept_manager -> procurement_manager -> finance_manager (approval-api
# crud/engine.py::_WORKFLOW_DEFAULTS), and GET /agreements/{id} is gated on
# epms.agreement.read (epms-api api/v1/agreements.py::AgrReadDep). Without a
# read grant the step-0 approver 403s on their own approve_agr task deep link
# and the nav entry is hidden, so no agreement can ever reach `active` and the
# invoice-match candidate pool stays permanently empty. director / gm / opm are
# included because an admin may configure any of them into the chain from
# Portal Admin without touching this table.
#
# requester is included so access_scope.visible_agreement_subquery's
# created_by / owner_id branches are reachable at all — an agreement's named
# owner is frequently a plain requester, and the read gate runs BEFORE scoping.
#
# NOTE: the chain's "gm_or_opm" is a PSEUDO-role resolved at task-assignment
# time into a real `gm` or `opm` (approval-api crud/engine.py::_resolve_gm_or_opm);
# it is NOT a role_defs code, and role_permissions.role_code is a FK to
# role_defs.code (identity 0002_authz_tables), so inserting it here would abort
# the migration with a ForeignKeyViolation. Grant the two real roles instead.
_GRANTS = {
    "epms.agreement.read": (
        "system_admin", "procurement_officer", "procurement_manager",
        "ap_clerk", "finance_bp", "finance_manager", "auditor",
        "dept_manager", "director", "gm", "opm", "requester",
    ),
    "epms.agreement.write": (
        "system_admin", "procurement_officer", "procurement_manager",
    ),
}


def upgrade() -> None:
    for key, (module, label, sort) in _KEYS.items():
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','{module}','{label}',{sort}) ON CONFLICT (key) DO NOTHING")
    for key, roles in _GRANTS.items():
        for role in roles:
            op.execute(
                "INSERT INTO role_permissions(role_code,permission_key) "
                f"VALUES ('{role}','{key}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Only the grants + defs this migration added. Do NOT touch role_defs —
    # every role referenced here pre-exists.
    for key in _KEYS:
        op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{key}'")
        op.execute(f"DELETE FROM permission_defs WHERE key = '{key}'")
