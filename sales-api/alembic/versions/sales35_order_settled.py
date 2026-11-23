"""sales35_order_settled — an order's Payment: a manual Settled mark set by Finance (user 2026-11-19 / 2026-11-23).

"When it is settled it means we received it; if there is a problem it is not part of this app." For now every
source (retailer, manual, Shopify) is marked by hand by a finance user (sales.settle): the date the money came, a
reference (bank / cheque / remittance no.), an optional amount and note, and who marked it when. Undo clears them
(audited with its reason). A later derived source (Shopify from the gateway settlements) goes beside these columns;
the manual mark wins.

All columns are new and empty: no data is touched.

Revision id "sales35_order_settled" is 21 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = 'sales35_order_settled'
down_revision = 'sales34_order_reason_parent'
branch_labels = None
depends_on = None

_COLUMNS = ('settled_on', 'settled_reference', 'settled_amount', 'settled_note', 'settled_at', 'settled_by', 'settled_by_name')


def upgrade() -> None:
    op.add_column('sales_orders', sa.Column('settled_on', sa.Date(), nullable=True))
    op.add_column('sales_orders', sa.Column('settled_reference', sa.String(100), nullable=True))
    op.add_column('sales_orders', sa.Column('settled_amount', sa.Numeric(14, 2), nullable=True))
    op.add_column('sales_orders', sa.Column('settled_note', sa.Text(), nullable=True))
    op.add_column('sales_orders', sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('sales_orders', sa.Column('settled_by', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('sales_orders', sa.Column('settled_by_name', sa.String(255), nullable=True))


def downgrade() -> None:
    for c in reversed(_COLUMNS):
        op.drop_column('sales_orders', c)
