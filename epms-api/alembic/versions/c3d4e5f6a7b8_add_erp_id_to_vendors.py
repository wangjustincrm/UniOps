"""add erp_id to vendors

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-01 00:00:01.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('vendors', sa.Column('erp_id', sa.String(100), nullable=True))
    op.create_index('ix_vendors_erp_id', 'vendors', ['erp_id'])


def downgrade() -> None:
    op.drop_index('ix_vendors_erp_id', table_name='vendors')
    op.drop_column('vendors', 'erp_id')
