"""sales15_order_ref — the Order ref and the invoice's delivery number (user 2026-11-03).

- `sales_orders.source_reference` is the Order ref (A.3: partner abbreviation,
  "-", the source order number; a manual order: its internal number). Filled
  where it is missing, and corrected for 850 orders that held the bare PO;
  then unique. Set once at creation from here on.
- `sales_partners.abbrev`: unique (case-insensitive) — two partners with one
  abbreviation would give two orders the same ref.
- `sales_invoices.delivery_number`: the retailer's proof-of-delivery /
  receiving number (free text ≤ 50; on the record and the PDF — A.9's 810 has
  no field for it).

Revision id "sales15_order_ref" is 17 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa

revision = 'sales15_order_ref'
down_revision = 'sales14_conn_schedule'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE sales_orders o SET source_reference = p.abbrev || '-' || o.source_order_number
          FROM sales_partners p
         WHERE p.code = o.partner_code AND coalesce(p.abbrev, '') <> '' AND coalesce(o.source_order_number, '') <> ''
           AND o.source_type <> 'MANUAL'
           AND (o.source_reference IS NULL OR (o.source_type = 'EDI_850' AND o.source_reference = o.source_order_number))
    """)
    op.execute("""
        UPDATE sales_orders o SET source_reference = p.abbrev || '-' || o.order_number
          FROM sales_partners p
         WHERE p.code = o.partner_code AND coalesce(p.abbrev, '') <> '' AND o.source_type = 'MANUAL'
           AND o.source_reference IS NULL
    """)
    op.create_index('uq_sales_orders_source_reference', 'sales_orders', ['source_reference'], unique=True,
                    postgresql_where=sa.text('source_reference IS NOT NULL'))
    op.create_index('uq_sales_partners_abbrev', 'sales_partners', [sa.text('upper(abbrev)')], unique=True,
                    postgresql_where=sa.text("abbrev IS NOT NULL AND abbrev <> ''"))
    op.add_column('sales_invoices', sa.Column('delivery_number', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('sales_invoices', 'delivery_number')
    op.drop_index('uq_sales_partners_abbrev', table_name='sales_partners')
    op.drop_index('uq_sales_orders_source_reference', table_name='sales_orders')
