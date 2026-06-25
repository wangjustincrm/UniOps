"""add role_permissions and custom_roles to company_config

Revision ID: h8c9d0e1f2g3
Revises: g7b8c9d0e1f2
Create Date: 2026-04-11 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = 'h8c9d0e1f2g3'
down_revision: Union[str, None] = 'g7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('company_config', sa.Column('role_permissions', JSONB, nullable=True))
    op.add_column('company_config', sa.Column('custom_roles', JSONB, nullable=True))
    # Back-fill existing rows with empty dicts/lists
    op.execute("UPDATE company_config SET role_permissions = '{}' WHERE role_permissions IS NULL")
    op.execute("UPDATE company_config SET custom_roles = '[]' WHERE custom_roles IS NULL")
    op.alter_column('company_config', 'role_permissions', nullable=False)
    op.alter_column('company_config', 'custom_roles', nullable=False)


def downgrade() -> None:
    op.drop_column('company_config', 'custom_roles')
    op.drop_column('company_config', 'role_permissions')
