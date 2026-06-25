"""Add TRV travel date fields and CFM custom_forms to policy config.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TRV travel period fields on expense_claims
    op.add_column('expense_claims', sa.Column('travel_from_date', sa.Date, nullable=True))
    op.add_column('expense_claims', sa.Column('travel_to_date',   sa.Date, nullable=True))
    op.add_column('expense_claims', sa.Column('travel_destination', sa.String(255), nullable=True))

    # CFM custom form definitions on expense_policy_config
    op.add_column('expense_policy_config', sa.Column(
        'custom_forms', JSONB, nullable=False, server_default='[]'
    ))
    # Incidental meal limit (for TRV)
    op.add_column('expense_policy_config', sa.Column(
        'meal_incidental_limit', sa.Numeric(8, 2), nullable=False, server_default='17.30'
    ))


def downgrade() -> None:
    op.drop_column('expense_claims', 'travel_from_date')
    op.drop_column('expense_claims', 'travel_to_date')
    op.drop_column('expense_claims', 'travel_destination')
    op.drop_column('expense_policy_config', 'custom_forms')
    op.drop_column('expense_policy_config', 'meal_incidental_limit')
