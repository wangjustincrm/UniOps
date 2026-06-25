"""Add over_budget flag and justification to purchase_requests.

Revision ID: r8m9n0o1p2q3
Revises: q7l8m9n0o1p2
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa

revision = 'r8m9n0o1p2q3'
down_revision = 'q7l8m9n0o1p2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'purchase_requests',
        sa.Column(
            'over_budget',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        'purchase_requests',
        sa.Column('over_budget_justification', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('purchase_requests', 'over_budget_justification')
    op.drop_column('purchase_requests', 'over_budget')
