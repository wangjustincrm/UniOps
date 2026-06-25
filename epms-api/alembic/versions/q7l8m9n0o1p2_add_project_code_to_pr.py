"""Add project_code to purchase_requests.

Revision ID: q7l8m9n0o1p2
Revises: p6k7l8m9n0o1
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = 'q7l8m9n0o1p2'
down_revision = 'p6k7l8m9n0o1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'purchase_requests',
        sa.Column('project_code', sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('purchase_requests', 'project_code')
