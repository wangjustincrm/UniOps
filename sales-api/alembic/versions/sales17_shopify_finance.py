"""sales17_shopify_finance — Shopify payments, refunds and the order's finance (user 2026-11-03).

Read-only from the store (read_orders): per Shopify order its transactions
(`sales_order_payments`, no card data), its refunds with their lines and
adjustments, and on the order the channel (source_name), tags, gateways,
discount codes, tax lines, original total, refunded and net; per line its tax
and discount. `finance_updated_at` is the Shopify update they were read from.

Revision id "sales17_shopify_finance" is 23 characters (limit 32).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = 'sales17_shopify_finance'
down_revision = 'sales16_945_reference'
branch_labels = None
depends_on = None


def _audited() -> list[sa.Column]:
    return [
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by', UUID(as_uuid=True), nullable=True),
        sa.Column('created_by_name', sa.String(255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by_name', sa.String(255), nullable=True),
    ]


def upgrade() -> None:
    for name, type_ in (('source_name', sa.String(60)), ('tags', sa.Text()), ('payment_gateways', JSONB()),
                        ('discount_codes', JSONB()), ('tax_lines', JSONB()), ('original_total', sa.Numeric(14, 2)),
                        ('total_refunded', sa.Numeric(14, 2)), ('net_amount', sa.Numeric(14, 2)),
                        ('finance_updated_at', sa.DateTime(timezone=True))):
        op.add_column('sales_orders', sa.Column(name, type_, nullable=True))
    for name, type_ in (('tax_amount', sa.Numeric(14, 2)), ('discount_amount', sa.Numeric(14, 2)), ('tax_lines', JSONB())):
        op.add_column('sales_order_lines', sa.Column(name, type_, nullable=True))

    op.create_table(
        'sales_order_refunds', *_audited(),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('sales_orders.id'), nullable=False),
        sa.Column('shopify_id', sa.BigInteger(), nullable=False),
        sa.Column('refunded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('restock', sa.Boolean(), nullable=True),
        sa.Column('lines_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('shipping_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('adjustments_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('raw', JSONB(), nullable=False),
        sa.UniqueConstraint('shopify_id', name='uq_sales_order_refunds_shopify'),
    )
    op.create_index('ix_sales_order_refunds_order_id', 'sales_order_refunds', ['order_id'])
    op.create_table(
        'sales_order_payments', *_audited(),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('sales_orders.id'), nullable=False),
        sa.Column('shopify_id', sa.BigInteger(), nullable=False),
        sa.Column('parent_id', sa.BigInteger(), nullable=True),
        sa.Column('refund_id', sa.Integer(), sa.ForeignKey('sales_order_refunds.id'), nullable=True),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('gateway', sa.String(80), nullable=True),
        sa.Column('status', sa.String(20), nullable=True),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('currency', sa.String(3), nullable=True),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('authorization', sa.String(255), nullable=True),
        sa.Column('payment_id', sa.String(100), nullable=True),
        sa.Column('error_code', sa.String(60), nullable=True),
        sa.Column('source_name', sa.String(60), nullable=True),
        sa.Column('test', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('raw', JSONB(), nullable=False),
        sa.UniqueConstraint('shopify_id', name='uq_sales_order_payments_shopify'),
    )
    op.create_index('ix_sales_order_payments_order_id', 'sales_order_payments', ['order_id'])
    op.create_table(
        'sales_order_refund_lines',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('refund_id', sa.Integer(), sa.ForeignKey('sales_order_refunds.id'), nullable=False),
        sa.Column('shopify_id', sa.BigInteger(), nullable=False),
        sa.Column('line_item_id', sa.BigInteger(), nullable=True),
        sa.Column('order_line_id', sa.Integer(), sa.ForeignKey('sales_order_lines.id'), nullable=True),
        sa.Column('sku', sa.String(100), nullable=True),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('subtotal', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('total_tax', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('restock_type', sa.String(20), nullable=True),
        sa.UniqueConstraint('shopify_id', name='uq_sales_order_refund_lines_shopify'),
    )
    op.create_index('ix_sales_order_refund_lines_refund_id', 'sales_order_refund_lines', ['refund_id'])
    op.create_table(
        'sales_order_refund_adjustments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('refund_id', sa.Integer(), sa.ForeignKey('sales_order_refunds.id'), nullable=False),
        sa.Column('shopify_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(40), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('tax_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.UniqueConstraint('shopify_id', name='uq_sales_order_refund_adjustments_shopify'),
    )
    op.create_index('ix_sales_order_refund_adjustments_refund_id', 'sales_order_refund_adjustments', ['refund_id'])


def downgrade() -> None:
    for t in ('sales_order_refund_adjustments', 'sales_order_refund_lines', 'sales_order_payments', 'sales_order_refunds'):
        op.drop_table(t)
    for c in ('tax_amount', 'discount_amount', 'tax_lines'):
        op.drop_column('sales_order_lines', c)
    for c in ('source_name', 'tags', 'payment_gateways', 'discount_codes', 'tax_lines', 'original_total',
              'total_refunded', 'net_amount', 'finance_updated_at'):
        op.drop_column('sales_orders', c)
