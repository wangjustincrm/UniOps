"""sales23_order_edi_mailbox — the buyer's TrueCommerce mailbox on the order (850 EDIMailbox),
sent back in its 810 (Sobeys: one per division). A nullable column, nothing else.

Revision id "sales23_order_edi_mailbox" is 25 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op

revision = 'sales23_order_edi_mailbox'
down_revision = 'sales22_open_orders_index'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sales_orders', sa.Column('edi_mailbox', sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column('sales_orders', 'edi_mailbox')
