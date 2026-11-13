"""sales29_order_agreement_id — the 850's "Agreement ID", kept on the order and sent back in its 810 when the 850
named one (user 2026-11-13). Loblaws send the PO number there, Walmart "SPECIAL INSTRUCTIONS"; others leave it empty.

Revision id "sales29_order_agreement_id" is 25 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op

revision = 'sales29_order_agreement_id'
down_revision = 'sales28_transfer_resolution'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sales_orders', sa.Column('agreement_id', sa.String(60), nullable=True))


def downgrade() -> None:
    op.drop_column('sales_orders', 'agreement_id')
