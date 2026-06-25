"""add gr_attachments and pa_attachments tables

Revision ID: j0e1f2g3h4i5
Revises: i9d0e1f2g3h4
Create Date: 2026-04-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'j0e1f2g3h4i5'
down_revision: Union[str, None] = 'i9d0e1f2g3h4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'gr_attachments',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('gr_id', sa.UUID(as_uuid=True), sa.ForeignKey('goods_receipts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('content_type', sa.String(100), nullable=False, server_default='application/octet-stream'),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('file_data', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_gr_attachments_gr_id', 'gr_attachments', ['gr_id'])

    op.create_table(
        'pa_attachments',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('pa_id', sa.UUID(as_uuid=True), sa.ForeignKey('payment_applications.id', ondelete='CASCADE'), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('content_type', sa.String(100), nullable=False, server_default='application/octet-stream'),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('file_data', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_pa_attachments_pa_id', 'pa_attachments', ['pa_id'])


def downgrade() -> None:
    op.drop_index('ix_pa_attachments_pa_id', table_name='pa_attachments')
    op.drop_table('pa_attachments')
    op.drop_index('ix_gr_attachments_gr_id', table_name='gr_attachments')
    op.drop_table('gr_attachments')
