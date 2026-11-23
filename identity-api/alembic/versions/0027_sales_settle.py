"""The Sales finance key and role: sales.settle (user 2026-11-23).

An order's Payment is marked Settled by hand by a finance user, for every source (retailer, manual, Shopify):
sales-api app/services/order_payment.py. The key is Finance's only — not on sales.read, not on
sales.order.operate, and sales_operator does NOT hold it.

* key  sales.settle   — Mark settled / Undo settled on an order (one, or all shown in bulk)
* role sales_finance  — "Sales Finance": sales.read + sales.settle. ADDITIONAL-ONLY (assignable_as_primary = false,
                        like sales_operator in 0023): a finance user keeps their own login role and gains this one;
                        adding people is an Access Control assignment (the user's choice, 2026-11-23).

system_admin is granted sales.settle explicitly (effective_permissions() does not short-circuit it, see 0013).

Order matters on an empty database: permission_defs and role_defs are written before role_permissions, whose FKs
point at both. Idempotent (ON CONFLICT DO NOTHING): safe on dev2, which already has 0023's keys.

Parent: 0026_merge_sales_hr (the single head). Revision id "0027_sales_settle" is 17 characters (limit 32).
"""
from alembic import op

revision = "0027_sales_settle"
down_revision = "0026_merge_sales_hr"
branch_labels = None
depends_on = None

_KEY, _KEY_LABEL, _SORT = "sales.settle", "Settle Sales Orders (Finance)", 157
_ROLE, _ROLE_LABEL = "sales_finance", "Sales Finance"
_GRANTS = ((_ROLE, "sales.read"), (_ROLE, _KEY), ("system_admin", _KEY))


def upgrade() -> None:
    op.execute("INSERT INTO permission_defs(key,module,label,sort) "
               f"VALUES ('{_KEY}','sales','{_KEY_LABEL}',{_SORT}) ON CONFLICT (key) DO NOTHING")
    op.execute("INSERT INTO role_defs(code,label,sort,is_active,assignable_as_primary) "
               f"VALUES ('{_ROLE}','{_ROLE_LABEL}',861,true,false) ON CONFLICT (code) DO NOTHING")
    for role, key in _GRANTS:
        op.execute("INSERT INTO role_permissions(role_code,permission_key) "
                   f"SELECT code, '{key}' FROM role_defs WHERE code = '{role}' ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.execute(f"DELETE FROM user_roles WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_permissions WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_defs WHERE code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{_KEY}'")
    op.execute(f"DELETE FROM permission_defs WHERE key = '{_KEY}'")
