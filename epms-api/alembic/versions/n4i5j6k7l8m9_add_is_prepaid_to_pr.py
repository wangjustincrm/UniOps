"""Add is_prepaid column to purchase_requests.

Revision ID: n4i5j6k7l8m9
Revises: m3h4i5j6k7l8
Create Date: 2026-05-12
"""
from alembic import op
import sqlalchemy as sa

revision = 'n4i5j6k7l8m9'
down_revision = 'm3h4i5j6k7l8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'purchase_requests',
        sa.Column('is_prepaid', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('purchase_requests', 'is_prepaid')
