"""add line_items to invoices

Revision ID: k1f2g3h4i5j6
Revises: j0e1f2g3h4i5
Create Date: 2026-04-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'k1f2g3h4i5j6'
down_revision: Union[str, None] = 'j0e1f2g3h4i5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'invoices',
        sa.Column('line_items', JSONB(), nullable=False, server_default='[]'),
    )


def downgrade() -> None:
    op.drop_column('invoices', 'line_items')
