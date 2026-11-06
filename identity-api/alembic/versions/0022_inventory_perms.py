"""Access Control keys for the Inventory module (inventory-api / inventory web).

Three keys, because quantities and money are different audiences (design §9):

* inventory.view       — every quantity: WMS, NC stock, IA/GL quantities,
                         the reconciliation and trace pages
* inventory.cost.view  — every amount: IA amounts and unit prices, GL amounts.
                         inventory-api strips amount fields server-side for
                         callers without it.
* inventory.admin      — Settings writes and the manual NC refresh

Only system_admin is granted (explicitly — effective_permissions() does not
short-circuit it, see 0013). The user assigns every other role in Portal →
Access Control (decision Q3, 2026-10-14), so no other default is seeded.

Parent: main's head 0021_fin_analysis_perm. feature/ehs-safety carries its own
0016–0020; whichever branch reaches production second must leave identity with
ONE head (see 0021's docstring). Check `alembic heads` before a release build.

Revision id "0022_inventory_perms" is 20 characters (limit 32).
"""
from alembic import op

revision = "0022_inventory_perms"
down_revision = "0021_fin_analysis_perm"
branch_labels = None
depends_on = None

_KEYS = (
    ("inventory.view", "View Inventory (quantities)", 140),
    ("inventory.cost.view", "View Inventory Amounts", 141),
    ("inventory.admin", "Inventory Settings & Sync", 142),
)


def upgrade() -> None:
    for key, label, sort in _KEYS:
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','inventory','{label}',{sort}) ON CONFLICT (key) DO NOTHING")
        op.execute(
            "INSERT INTO role_permissions(role_code,permission_key) "
            f"SELECT code, '{key}' FROM role_defs WHERE code = 'system_admin' "
            "ON CONFLICT DO NOTHING")


def downgrade() -> None:
    keys = ",".join(f"'{k}'" for k, _l, _s in _KEYS)
    op.execute(f"DELETE FROM role_permissions WHERE permission_key IN ({keys})")
    op.execute(f"DELETE FROM permission_defs WHERE key IN ({keys})")
