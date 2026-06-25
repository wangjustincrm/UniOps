"""Add is_prepaid column to purchase_orders.

Revision ID: o5j6k7l8m9n0
Revises: n4i5j6k7l8m9
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = 'o5j6k7l8m9n0'
down_revision = 'n4i5j6k7l8m9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'purchase_orders',
        sa.Column('is_prepaid', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('purchase_orders', 'is_prepaid')
