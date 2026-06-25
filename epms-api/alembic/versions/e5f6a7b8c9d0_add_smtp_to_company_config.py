"""add SMTP columns to company_config

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('company_config', sa.Column('smtp_host', sa.String(255), nullable=True))
    op.add_column('company_config', sa.Column('smtp_port', sa.Integer(), nullable=True))
    op.add_column('company_config', sa.Column('smtp_user', sa.String(255), nullable=True))
    op.add_column('company_config', sa.Column('smtp_password', sa.String(255), nullable=True))
    op.add_column('company_config', sa.Column('smtp_use_tls', sa.Boolean(), nullable=True))
    op.add_column('company_config', sa.Column('smtp_from', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('company_config', 'smtp_from')
    op.drop_column('company_config', 'smtp_use_tls')
    op.drop_column('company_config', 'smtp_password')
    op.drop_column('company_config', 'smtp_user')
    op.drop_column('company_config', 'smtp_port')
    op.drop_column('company_config', 'smtp_host')
