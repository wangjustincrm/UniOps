"""Seed the payment_officer role + its Access Control Matrix grants.

payment_officer is an ADDITIONAL role (grantable to many users, never a base
login role) that owns the Process-Payment task split out of ap_clerk. It gets
read visibility of the payables chain, plus view_finance so Portal's finance
nav (Payments Hub / Payment Batches / Remittance) is reachable, not just the
per-PA "process" action inside EPMS. Payment authority itself is enforced in
finance-api's _PAY_ROLES, not by a matrix permission.

2026-08-13 whole-phase-review fix: view_finance added here (was missing from
the original grant set — see §3 IMPORTANT finding in the phase review). Safe
to edit this file in place because it had not shipped anywhere at review time;
confirmed via `SELECT version_num FROM alembic_version_identity` against the
local dev DB and by grep of prod release notes in MEMORY.md before editing.

All inserts are ON CONFLICT DO NOTHING so this is safe to re-run, and
self-sufficient on a fresh DB (it seeds the permission_defs rows it references
so the role_permissions FKs resolve).
"""
from alembic import op

revision = "0008_payment_officer_role"
down_revision = "0007_receipt_write_perm"
branch_labels = None
depends_on = None

_ROLE = "payment_officer"
_LABEL = "Payment Officer"

_PERM_DEFS = {
    "view_po":      ("epms", "View Po", 1),
    "view_invoice": ("epms", "View Invoice", 3),
    "view_pa":      ("epms", "View Pa", 4),
    # Portal's finance nav (navConfig.tsx) gates Payments Hub / Payment
    # Batches / Remittance on view_finance — without this grant
    # payment_officer can pay one PA at a time from EPMS but cannot reach
    # batch payment at all, even though finance-api's own gate already
    # permits it (_check_can_pay / _FINANCE_ROLES). Module/label/sort match
    # the row seed_authz.py / earlier migrations already produce for this
    # key (module="finance", sort=14) so ON CONFLICT DO NOTHING is a true
    # no-op wherever the key already exists.
    "view_finance": ("finance", "View Finance", 14),
}
_GRANTS = ["view_po", "view_invoice", "view_pa", "view_finance"]
_LOCKS = ["view_pa"]


def upgrade() -> None:
    for key, (module, label, sort) in _PERM_DEFS.items():
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','{module}','{label}',{sort}) "
            "ON CONFLICT (key) DO NOTHING")
    op.execute(
        "INSERT INTO role_defs(code,label,sort,is_active) "
        f"VALUES ('{_ROLE}','{_LABEL}',851,true) ON CONFLICT (code) DO NOTHING")
    for key in _GRANTS:
        op.execute(
            "INSERT INTO role_permissions(role_code,permission_key) "
            f"VALUES ('{_ROLE}','{key}') ON CONFLICT DO NOTHING")
    for key in _LOCKS:
        op.execute(
            "INSERT INTO role_permission_locks(role_code,permission_key) "
            f"VALUES ('{_ROLE}','{key}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.execute(f"DELETE FROM role_permission_locks WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_permissions WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_defs WHERE code = '{_ROLE}'")
