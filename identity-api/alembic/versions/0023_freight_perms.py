"""Access Control keys for EPMS Freight (courier freight payment).

Design: docs/superpowers/specs/2026-10-19-freight-payment-design.md §8.2.

* epms.freight.register — register / edit / void one's own shipments (buyers)
* epms.freight.manage   — upload and match carrier invoices, raise Freight PAs,
                          see every shipment (the supply-chain person)
* epms.freight.admin    — maintain the carrier list

Defaults: register → procurement_officer and procurement_manager (the buyers
who book courier pickups); manage and admin → system_admin only, granted
explicitly because effective_permissions() does not short-circuit it (0013).
The supply-chain person who raises the PAs is named by the user and given
manage in Portal → Access Control.

Parent: main's head 0022_inventory_perms. Revision id is 17 characters (limit 32).
"""
from alembic import op

revision = "0023_freight_perms"
down_revision = "0022_inventory_perms"
branch_labels = None
depends_on = None

_KEYS = (
    ("epms.freight.register", "Register Courier Shipments", 125,
     ("system_admin", "procurement_officer", "procurement_manager")),
    ("epms.freight.manage", "Match Courier Invoices & Raise Freight PAs", 126, ("system_admin",)),
    ("epms.freight.admin", "Maintain Courier Carriers", 127, ("system_admin",)),
)


def upgrade() -> None:
    for key, label, sort, roles in _KEYS:
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','epms','{label}',{sort}) ON CONFLICT (key) DO NOTHING")
        codes = ",".join(f"'{r}'" for r in roles)
        # Only roles that exist: a grant to a missing role_defs row would hit the
        # FK and abort the whole migration in production.
        op.execute(
            "INSERT INTO role_permissions(role_code,permission_key) "
            f"SELECT code, '{key}' FROM role_defs WHERE code IN ({codes}) "
            "ON CONFLICT DO NOTHING")


def downgrade() -> None:
    keys = ",".join(f"'{k}'" for k, *_ in _KEYS)
    op.execute(f"DELETE FROM role_permissions WHERE permission_key IN ({keys})")
    op.execute(f"DELETE FROM permission_defs WHERE key IN ({keys})")
