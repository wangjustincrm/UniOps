"""Seed the erp_pa_officer role + its Access Control Matrix grants.

erp_pa_officer is an ADDITIONAL role (grantable to many users, never a base
login role) that owns the Create-PA task for ERP-imported, PR-less purchase
orders (NC). It gets read visibility of the procurement chain plus the
epms.pa.write gate so its holders can raise Payment Applications.

All inserts are ON CONFLICT DO NOTHING so this is safe to run on a DB that the
seed scripts (seed_authz / seed_phase2_keys) already touched, and self-
sufficient on a fresh DB (it seeds the permission_defs rows it references so the
role_permissions FKs resolve).
"""
from alembic import op

revision = "0004_erp_pa_officer_role"
down_revision = "0003_post_role_singleton"
branch_labels = None
depends_on = None

_ROLE = "erp_pa_officer"
_LABEL = "ERP PA Officer"

# permission_key -> (module, label, sort) — used only to backfill a def row when
# the seeds have not registered the key yet. Matches seed_authz / seed_phase2.
_PERM_DEFS = {
    "view_pr":       ("epms", "View Pr", 0),
    "view_po":       ("epms", "View Po", 1),
    "view_gr":       ("epms", "View Gr", 2),
    "view_invoice":  ("epms", "View Invoice", 3),
    "view_pa":       ("epms", "View Pa", 4),
    "epms.pa.write": ("epms", "Create / Edit PAs", 102),
}
# Granted cells (matrix): full chain visibility + the PA-write gate.
_GRANTS = ["view_pr", "view_po", "view_gr", "view_invoice", "view_pa", "epms.pa.write"]
# Locked cells (UI-uneditable): the two the role is functionally broken without.
_LOCKS = ["view_po", "view_pa"]


def upgrade() -> None:
    # 1. Ensure the permission defs the grants reference exist (idempotent).
    for key, (module, label, sort) in _PERM_DEFS.items():
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','{module}','{label}',{sort}) "
            "ON CONFLICT (key) DO NOTHING")
    # 2. The role itself (sort 850 → after built-ins, before custom roles at 900).
    op.execute(
        "INSERT INTO role_defs(code,label,sort,is_active) "
        f"VALUES ('{_ROLE}','{_LABEL}',850,true) ON CONFLICT (code) DO NOTHING")
    # 3. Matrix grants.
    for key in _GRANTS:
        op.execute(
            "INSERT INTO role_permissions(role_code,permission_key) "
            f"VALUES ('{_ROLE}','{key}') ON CONFLICT DO NOTHING")
    # 4. Locks.
    for key in _LOCKS:
        op.execute(
            "INSERT INTO role_permission_locks(role_code,permission_key) "
            f"VALUES ('{_ROLE}','{key}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Remove grants/locks/assignments then the role. permission_defs are left in
    # place (they may be shared/registered by the seeds).
    op.execute(f"DELETE FROM role_permission_locks WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_permissions WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM user_roles WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_defs WHERE code = '{_ROLE}'")
