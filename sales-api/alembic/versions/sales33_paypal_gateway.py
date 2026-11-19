"""sales33_paypal_gateway — PayPal as a second payment gateway (Finance, user 2026-11-19).

PayPal's Transaction Search, read-only, into the same tables as Authorize.Net: a connection learns
the kind PAYPAL, a document the system PAYPAL, and the gateway tables the gateway PAYPAL. Nothing
else changes: no new column, no data touched.

The downgrade refuses (changing nothing) while any PayPal row exists — a connection, a document, a
settlement or a transaction — since the narrower checks would fail on it half-way (as sales32's).

Revision id "sales33_paypal_gateway" is 21 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op

revision = 'sales33_paypal_gateway'
down_revision = 'sales32_gateway_settlements'
branch_labels = None
depends_on = None

CONN_KINDS = ("SFTP", "SHOPIFY", "AUTHORIZE_NET")
SYSTEMS = ("RADIANT", "TRUECOMMERCE", "SHOPIFY", "EMAIL", "AUTHORIZE_NET")
GATEWAYS = ("AUTHORIZE_NET",)


def _ck(table: str, name: str, column: str, values) -> None:
    op.drop_constraint(name, table, type_='check')
    op.create_check_constraint(name, table, f"{column} IN ({', '.join(repr(v) for v in values)})")


def upgrade() -> None:
    _ck('sales_connections', 'ck_sales_connections_kind', 'kind', CONN_KINDS + ("PAYPAL",))
    _ck('sales_documents', 'ck_sales_documents_system', 'system', SYSTEMS + ("PAYPAL",))
    _ck('sales_gateway_settlements', 'ck_sales_gateway_settlements_gateway', 'gateway', GATEWAYS + ("PAYPAL",))
    _ck('sales_gateway_transactions', 'ck_sales_gateway_transactions_gateway', 'gateway', GATEWAYS + ("PAYPAL",))


def downgrade() -> None:
    bind = op.get_bind()
    found = {
        "connection(s)": "SELECT count(*) FROM sales_connections WHERE kind = 'PAYPAL'",
        "document(s)": "SELECT count(*) FROM sales_documents WHERE system = 'PAYPAL'",
        "settlement(s)": "SELECT count(*) FROM sales_gateway_settlements WHERE gateway = 'PAYPAL'",
        "transaction(s)": "SELECT count(*) FROM sales_gateway_transactions WHERE gateway = 'PAYPAL'",
    }
    there = {what: bind.execute(sa.text(q)).scalar() for what, q in found.items()}
    if any(there.values()):
        raise RuntimeError("sales33 downgrade refused: PayPal rows exist ("
                           + ", ".join(f"{n} {what}" for what, n in there.items() if n)
                           + "). Delete them first; nothing was changed.")
    _ck('sales_gateway_transactions', 'ck_sales_gateway_transactions_gateway', 'gateway', GATEWAYS)
    _ck('sales_gateway_settlements', 'ck_sales_gateway_settlements_gateway', 'gateway', GATEWAYS)
    _ck('sales_documents', 'ck_sales_documents_system', 'system', SYSTEMS)
    _ck('sales_connections', 'ck_sales_connections_kind', 'kind', CONN_KINDS)
