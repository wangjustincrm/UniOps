"""sales34_order_reason_parent — why a manual order was made, and the order it repeats (user 2026-11-19).

"Reorder for this customer": a Draft manual order copied from any order (its ship-to, contact, carrier; no lines),
linked to it by `parent_order_id`. A manual order says why it exists (`manual_reason`: Gift, Replacement, Sample,
Staff purchase, Phone order, Other — a configurable list, required to submit) so Finance can see the free goods.
Both columns are new and empty: no data is touched.

Revision id "sales34_order_reason_parent" is 26 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op

revision = 'sales34_order_reason_parent'
down_revision = 'sales33_paypal_gateway'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sales_orders', sa.Column('manual_reason', sa.String(40), nullable=True))
    op.add_column('sales_orders', sa.Column('parent_order_id', sa.Integer(),
                                            sa.ForeignKey('sales_orders.id', name='fk_sales_orders_parent_order'), nullable=True))
    op.create_index('ix_sales_orders_parent_order_id', 'sales_orders', ['parent_order_id'])


def downgrade() -> None:
    op.drop_index('ix_sales_orders_parent_order_id', table_name='sales_orders')
    op.drop_column('sales_orders', 'parent_order_id')
    op.drop_column('sales_orders', 'manual_reason')
