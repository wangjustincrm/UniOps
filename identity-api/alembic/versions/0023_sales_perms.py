"""Access Control keys and the operator role for the Sales module (sales-api / sales web).

Seven keys (design docs/superpowers/specs/2026-10-23-order-bridge-design.md §6):

* sales.read             — every GET endpoint and every page
* sales.order.operate    — hold release, Send 940 now, delivery confirmation,
                           manual orders, review resolution, cancel / return
* sales.file.retry       — resend / reparse / ignore documents, run now
* sales.transfer.manage  — create, send, cancel transfers; review receipts
* sales.inventory.adjust — reserved for the next version (no endpoint in v1)
* sales.invoice.manage   — edit, send, resend invoices
* sales.config           — configuration screens and CSV import / export

system_admin is granted all seven explicitly (effective_permissions() does not
short-circuit it, see 0013). Role sales_operator holds all seven (D21); adding
people later is an Access Control assignment only. It is ADDITIONAL-ONLY
(assignable_as_primary = false, like finance_director in 0015): the operator
keeps their own login role and gains this one on top.

Order matters on an empty database: permission_defs and role_defs are written
before role_permissions, whose FKs point at both.

Parent: 0022_inventory_perms (single head). feature/ehs-safety carries its own
0016–0020; whichever branch reaches production second must leave identity with
ONE head (see 0021's docstring). Check `alembic heads` before a release build.

Revision id "0023_sales_perms" is 16 characters (limit 32).
"""
from alembic import op

revision = "0023_sales_perms"
down_revision = "0022_inventory_perms"
branch_labels = None
depends_on = None

_KEYS = (
    ("sales.read", "View Sales", 150),
    ("sales.order.operate", "Operate Sales Orders", 151),
    ("sales.file.retry", "Retry Sales Files", 152),
    ("sales.transfer.manage", "Manage Sales Transfers", 153),
    ("sales.inventory.adjust", "Adjust Sales Inventory (reserved)", 154),
    ("sales.invoice.manage", "Manage Sales Invoices", 155),
    ("sales.config", "Sales Configuration", 156),
)
_ROLE = "sales_operator"
_LABEL = "Sales Operator"


def upgrade() -> None:
    for key, label, sort in _KEYS:
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','sales','{label}',{sort}) ON CONFLICT (key) DO NOTHING")
    op.execute(
        "INSERT INTO role_defs(code,label,sort,is_active,assignable_as_primary) "
        f"VALUES ('{_ROLE}','{_LABEL}',860,true,false) ON CONFLICT (code) DO NOTHING")
    for key, _label, _sort in _KEYS:
        op.execute(
            "INSERT INTO role_permissions(role_code,permission_key) "
            f"SELECT code, '{key}' FROM role_defs WHERE code IN ('system_admin','{_ROLE}') "
            "ON CONFLICT DO NOTHING")


def downgrade() -> None:
    keys = ",".join(f"'{k}'" for k, _l, _s in _KEYS)
    op.execute(f"DELETE FROM user_roles WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_permissions WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_defs WHERE code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_permissions WHERE permission_key IN ({keys})")
    op.execute(f"DELETE FROM permission_defs WHERE key IN ({keys})")
