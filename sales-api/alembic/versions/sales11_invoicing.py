"""sales11_invoicing — delivery confirmation, invoices, and cancellations /
returns after shipment (P6; FR-OB-19, 30 … 35).

- `sales_delivery_confirmations`: one per order (FR-OB-31) — the confirmed date
  (= the invoice date); the delivered quantity per line is
  `sales_order_lines.delivered_qty` (sales05).
- `sales_invoices`: `IN-<order number>`, one per order; method, date, totals,
  terms, the document it was last sent as; edits are audited (FR-OB-35).
- `sales_invoice_lines`: per order line, in cases (UOM CA) at the 850 unit price.
- `sales_order_returns`: a retailer order cancelled or returned after shipment,
  with quantities and reason (FR-OB-19).

Revision id "sales11_invoicing" is 17 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'sales11_invoicing'
down_revision = 'sales10_shipments'
branch_labels = None
depends_on = None


def _audit():
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_name', sa.String(length=255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        'sales_delivery_confirmations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('confirmed_date', sa.Date(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        *_audit(),
        sa.ForeignKeyConstraint(['order_id'], ['sales_orders.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_id', name='uq_sales_delivery_confirmations_order'),
    )
    op.create_table(
        'sales_invoices',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('invoice_number', sa.String(length=40), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=False),
        sa.Column('invoice_date', sa.Date(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('payment_terms', sa.String(length=100), nullable=True),
        sa.Column('subtotal', sa.Numeric(14, 2), nullable=False),
        sa.Column('total', sa.Numeric(14, 2), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('send_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        *_audit(),
        sa.ForeignKeyConstraint(['order_id'], ['sales_orders.id']),
        sa.ForeignKeyConstraint(['document_id'], ['sales_documents.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invoice_number', name='uq_sales_invoices_number'),
        sa.UniqueConstraint('order_id', name='uq_sales_invoices_order'),
    )
    op.create_table(
        'sales_invoice_lines',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('invoice_id', sa.Integer(), nullable=False),
        sa.Column('line_no', sa.Integer(), nullable=False),
        sa.Column('order_line_id', sa.Integer(), nullable=True),
        sa.Column('item_code', sa.String(length=50), nullable=True),
        sa.Column('buyer_part', sa.String(length=50), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('uom', sa.String(length=10), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False),
        sa.Column('unit_price', sa.Numeric(14, 5), nullable=False),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(['invoice_id'], ['sales_invoices.id']),
        sa.ForeignKeyConstraint(['order_line_id'], ['sales_order_lines.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invoice_id', 'line_no', name='uq_sales_invoice_lines_line'),
    )
    op.create_table(
        'sales_order_returns',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('lines', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_audit()[:3],  # created only: a record is never edited
        sa.CheckConstraint("kind IN ('Cancelled after shipment', 'Returned')", name='ck_sales_order_returns_kind'),
        sa.ForeignKeyConstraint(['order_id'], ['sales_orders.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sales_order_returns_order_id', 'sales_order_returns', ['order_id'])


def downgrade() -> None:
    op.drop_index('ix_sales_order_returns_order_id', table_name='sales_order_returns')
    op.drop_table('sales_order_returns')
    op.drop_table('sales_invoice_lines')
    op.drop_table('sales_invoices')
    op.drop_table('sales_delivery_confirmations')
