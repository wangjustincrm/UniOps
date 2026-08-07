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

# Read: everyone already in the procurement/AP chain. Write: the roles that
# already hold epms.po.write, plus procurement_manager who owns the terms.
_GRANTS = {
    "epms.agreement.read": (
        "system_admin", "procurement_officer", "procurement_manager",
        "ap_clerk", "finance_bp", "finance_manager", "auditor",
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
