"""Add max_prepayment_pct to vendors, prepayment_pa_id to payment_applications.

Revision ID: p6k7l8m9n0o1
Revises: o5j6k7l8m9n0
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = 'p6k7l8m9n0o1'
down_revision = 'o5j6k7l8m9n0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'vendors',
        sa.Column('max_prepayment_pct', sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        'payment_applications',
        sa.Column(
            'prepayment_pa_id',
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey('payment_applications.id', ondelete='RESTRICT'),
            nullable=True,
        ),
    )
    op.create_index('ix_payment_applications_prepayment_pa_id', 'payment_applications', ['prepayment_pa_id'])


def downgrade() -> None:
    op.drop_index('ix_payment_applications_prepayment_pa_id', 'payment_applications')
    op.drop_column('payment_applications', 'prepayment_pa_id')
    op.drop_column('vendors', 'max_prepayment_pct')
