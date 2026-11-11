"""sales22_open_orders_index — a partial index on the orders that are not historical
(app/models/indexes.py): the working lists read only those. Index only.

Revision id "sales22_open_orders_index" is 25 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op

revision = 'sales22_open_orders_index'
down_revision = 'sales21_lookup_indexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_sales_orders_open', 'sales_orders', ['order_number'], postgresql_where=sa.text('historical IS false'))


def downgrade() -> None:
    op.drop_index('ix_sales_orders_open', table_name='sales_orders')
