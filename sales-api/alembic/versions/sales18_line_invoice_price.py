"""sales18_line_invoice_price — the invoice price on the order line (user 2026-11-03).

`sales_order_lines.invoice_unit_price`: the price the invoice will carry for
the line, per order unit (as the PO price), set before an invoice exists.
NULL means "the PO price" — the default. The invoice draft copies it instead
of the PO price; after the draft exists the price is edited on the invoice.

Revision id "sales18_line_invoice_price" is 25 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa

revision = 'sales18_line_invoice_price'
down_revision = 'sales17_shopify_finance'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sales_order_lines', sa.Column('invoice_unit_price', sa.Numeric(14, 5), nullable=True))


def downgrade() -> None:
    op.drop_column('sales_order_lines', 'invoice_unit_price')
