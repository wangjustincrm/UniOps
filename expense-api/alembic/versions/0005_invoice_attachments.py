"""Invoice attachments table — shared storage for EPMS and OA invoice files.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-01
"""
import uuid
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'invoice_attachments',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('invoice_id', UUID(as_uuid=True), nullable=False),
        sa.Column('invoice_source', sa.String(10), nullable=False),  # 'epms' | 'oa'
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('content_type', sa.String(100), nullable=False, server_default='application/octet-stream'),
        sa.Column('file_size_bytes', sa.BigInteger, nullable=False),
        sa.Column('file_data', sa.LargeBinary, nullable=False),
        sa.Column('uploaded_by', UUID(as_uuid=True), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        'ix_invoice_attachments_invoice',
        'invoice_attachments', ['invoice_id', 'invoice_source']
    )


def downgrade() -> None:
    op.drop_table('invoice_attachments')
