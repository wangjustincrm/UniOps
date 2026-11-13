"""sales31_count_line_picked — what Radiant's report shows picked for an order, per lot (its "Allocation"
column: CRM|<order number>): {"1008724": 600}. With the order still not shipped, it is that lot's "in process".

Revision id "sales31_count_line_picked" is 25 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = 'sales31_count_line_picked'
down_revision = 'sales30_stock_counts'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sales_stock_count_lines', sa.Column('picked', JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column('sales_stock_count_lines', 'picked')
