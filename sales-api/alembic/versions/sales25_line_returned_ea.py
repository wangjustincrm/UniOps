"""sales25_line_returned_ea — what the customer returned (or refused) of what shipped, per order
line, entered by a person before the invoice goes (user 2026-11-12). Delivered = shipped − returned;
the invoice bills the delivered. A column with a default, nothing else.

Revision id "sales25_line_returned_ea" is 24 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op

revision = 'sales25_line_returned_ea'
down_revision = 'sales24_connection_read_write'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sales_order_lines', sa.Column('returned_ea', sa.Integer(), nullable=False, server_default='0'))
    op.create_check_constraint('ck_sales_order_lines_returned_ea', 'sales_order_lines', 'returned_ea >= 0')


def downgrade() -> None:
    op.drop_constraint('ck_sales_order_lines_returned_ea', 'sales_order_lines', type_='check')
    op.drop_column('sales_order_lines', 'returned_ea')
