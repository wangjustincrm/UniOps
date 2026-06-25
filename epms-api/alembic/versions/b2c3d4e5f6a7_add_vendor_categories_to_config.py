"""add vendor_categories to company_config

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'company_config',
        sa.Column(
            'vendor_categories',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='["Raw Materials", "IT", "Services", "Office Supplies", "Maintenance", "Logistics"]',
        ),
    )


def downgrade() -> None:
    op.drop_column('company_config', 'vendor_categories')
