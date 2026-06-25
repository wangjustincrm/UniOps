"""add place_order fields to purchase_orders

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'g7b8c9d0e1f2'
down_revision: Union[str, None] = 'e5f6a7b8c9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('purchase_orders', sa.Column('place_order_method', sa.String(10), nullable=True))
    op.add_column('purchase_orders', sa.Column('place_order_email_to', sa.String(255), nullable=True))
    op.add_column('purchase_orders', sa.Column('place_order_reference', sa.String(255), nullable=True))
    op.add_column('purchase_orders', sa.Column('placed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('purchase_orders', 'placed_at')
    op.drop_column('purchase_orders', 'place_order_reference')
    op.drop_column('purchase_orders', 'place_order_email_to')
    op.drop_column('purchase_orders', 'place_order_method')
