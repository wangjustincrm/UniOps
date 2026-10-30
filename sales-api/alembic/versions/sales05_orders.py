"""sales05_orders — orders and their lines (design §3.3, P3).

sales_orders: one row per source order, unique on (partner_code,
source_order_id) — the same order arriving again updates it (AC-OB-16).
partner_code has no foreign key on purpose: an 850 naming an unknown partner
is still created and goes to Review (FR-OB-15). Fulfillment and invoice
statuses with their `_before_hold` memories (§13.6); the order state is
derived (D17). sales_order_number_seq starts at 1; P9 moves it above Zoho's
highest. sales_order_lines: unit, factor and EA quantity per line (FR-OB-14),
the identifiers received (FR-OB-15). 940s, shipments, confirmations and
invoices come with P4–P6 (§3.6 renumbered 2026-10-29).

Revision id "sales05_orders" is 14 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'sales05_orders'
down_revision = 'sales04_shopify_channel'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence('sales_order_number_seq', start=1)))
    op.create_table('sales_orders',
    sa.Column('order_number', sa.BigInteger(), server_default=sa.text("nextval('sales_order_number_seq')"), nullable=False),
    sa.Column('partner_code', sa.String(length=50), nullable=False),
    sa.Column('connection_id', sa.Integer(), nullable=True),
    sa.Column('source_type', sa.String(length=10), nullable=False),
    sa.Column('source_order_id', sa.String(length=64), nullable=False),
    sa.Column('source_order_number', sa.String(length=50), nullable=True),
    sa.Column('source_reference', sa.String(length=80), nullable=True),
    sa.Column('order_type', sa.String(length=20), nullable=True),
    sa.Column('department', sa.String(length=20), nullable=True),
    sa.Column('order_date', sa.Date(), nullable=True),
    sa.Column('requested_delivery_date', sa.Date(), nullable=True),
    sa.Column('do_not_deliver_after', sa.Date(), nullable=True),
    sa.Column('cancel_date', sa.Date(), nullable=True),
    sa.Column('ship_to', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('bill_to', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('contact', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('currency', sa.String(length=3), server_default='CAD', nullable=False),
    sa.Column('subtotal', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('tax', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('shipping', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('discount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('total', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('fulfillment_status', sa.String(length=20), nullable=False),
    sa.Column('fulfillment_status_before_hold', sa.String(length=20), nullable=True),
    sa.Column('invoice_status', sa.String(length=40), nullable=False),
    sa.Column('invoice_status_before_hold', sa.String(length=40), nullable=True),
    sa.Column('hold_reason', sa.String(length=40), nullable=True),
    sa.Column('review_reason', sa.Text(), nullable=True),
    sa.Column('historical', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('first_received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_in_shopify_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archive_suppressed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('shopify_financial_status', sa.String(length=30), nullable=True),
    sa.Column('shopify_fulfillment_status', sa.String(length=30), nullable=True),
    sa.Column('shopify_fulfillment_id', sa.String(length=40), nullable=True),
    sa.Column('warehouse_order_number_received', sa.String(length=40), nullable=True),
    sa.Column('zoho_order_id', sa.String(length=40), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_by_name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("fulfillment_status IN ('Draft', 'Review', 'Payment Hold', 'Pending', 'Archive Suppressed', '940 Generated', '940 Sent', '940 Accepted', '940 Rejected', '940 Timeout', '940 Failed', 'Shipped', 'On Hold', 'Cancelled')", name='ck_sales_orders_fulfillment_status'),
    sa.CheckConstraint("hold_reason IN ('Payment Required')", name='ck_sales_orders_hold_reason'),
    sa.CheckConstraint("invoice_status IN ('Not Required', 'Pending', 'Awaiting Requested Date', 'Waiting for Delivery Confirmation', 'Ready', 'Queued', 'Sent', 'Failed', 'Blocked')", name='ck_sales_orders_invoice_status'),
    sa.CheckConstraint("source_type IN ('SHOPIFY', 'EDI_850', 'MANUAL')", name='ck_sales_orders_source_type'),
    sa.ForeignKeyConstraint(['connection_id'], ['sales_connections.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('order_number'),
    sa.UniqueConstraint('partner_code', 'source_order_id', name='uq_sales_orders_source')
    )
    op.create_index('ix_sales_orders_fulfillment_status', 'sales_orders', ['fulfillment_status'], unique=False)
    op.create_table('sales_order_lines',
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('line_no', sa.BigInteger(), nullable=False),
    sa.Column('item_code', sa.String(length=50), nullable=True),
    sa.Column('source', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('ordered_qty', sa.Numeric(precision=14, scale=4), nullable=False),
    sa.Column('order_unit', sa.String(length=20), nullable=True),
    sa.Column('factor_ea', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('qty_ea', sa.Integer(), nullable=True),
    sa.Column('unit_price', sa.Numeric(precision=14, scale=5), nullable=True),
    sa.Column('line_status', sa.String(length=20), server_default='Active', nullable=False),
    sa.Column('shipped_ea', sa.Integer(), nullable=True),
    sa.Column('delivered_qty', sa.Integer(), nullable=True),
    sa.Column('removed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('problem', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('updated_by_name', sa.String(length=255), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_by_name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("line_status IN ('Active', 'Shipped', 'Short Shipped', 'Cancelled', 'Error')", name='ck_sales_order_lines_status'),
    sa.ForeignKeyConstraint(['item_code'], ['sales_items.item_code'], ),
    sa.ForeignKeyConstraint(['order_id'], ['sales_orders.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('order_id', 'line_no', name='uq_sales_order_lines_line')
    )
    op.create_index(op.f('ix_sales_order_lines_order_id'), 'sales_order_lines', ['order_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_sales_order_lines_order_id'), table_name='sales_order_lines')
    op.drop_table('sales_order_lines')
    op.drop_index('ix_sales_orders_fulfillment_status', table_name='sales_orders')
    op.drop_table('sales_orders')
    op.execute(sa.schema.DropSequence(sa.Sequence('sales_order_number_seq')))
