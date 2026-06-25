"""add_po_attachments

Revision ID: 7f71e88f425f
Revises: m3h4i5j6k7l8
Create Date: 2026-05-11 12:01:03.083743
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '7f71e88f425f'
down_revision: Union[str, None] = 'm3h4i5j6k7l8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'po_attachments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('po_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False, server_default='application/octet-stream'),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('file_data', sa.LargeBinary(), nullable=True),
        sa.Column('storage_key', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_po_attachments_po_id', 'po_attachments', ['po_id'])


def downgrade() -> None:
    op.drop_index('ix_po_attachments_po_id', table_name='po_attachments')
    op.drop_table('po_attachments')
