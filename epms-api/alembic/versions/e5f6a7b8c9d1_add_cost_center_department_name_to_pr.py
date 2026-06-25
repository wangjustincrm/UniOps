"""add cost_center_name and department_name to purchase_requests

Revision ID: e5f6a7b8c9d1
Revises: d4f5a6b7c8d9
Create Date: 2026-04-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'e5f6a7b8c9d1'
down_revision = 'd4f5a6b7c8d9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('purchase_requests', sa.Column('cost_center_name', sa.String(255), nullable=True))
    op.add_column('purchase_requests', sa.Column('department_name', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('purchase_requests', 'department_name')
    op.drop_column('purchase_requests', 'cost_center_name')
