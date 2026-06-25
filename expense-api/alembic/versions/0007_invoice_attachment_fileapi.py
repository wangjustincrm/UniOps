"""Migrate invoice_attachments to file-api: add storage_key, drop file_data.

All new uploads go to file-api (:8005) and store the returned UUID in storage_key.
The file_data column (LargeBinary) is dropped; legacy blobs are not migrated
(any existing rows should be re-uploaded if needed).

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add storage_key — UUID pointing to file-api record
    op.add_column('invoice_attachments',
        sa.Column('storage_key', UUID(as_uuid=True), nullable=True, index=True)
    )
    # Drop the binary blob column
    op.drop_column('invoice_attachments', 'file_data')


def downgrade() -> None:
    op.drop_column('invoice_attachments', 'storage_key')
    op.add_column('invoice_attachments',
        sa.Column('file_data', sa.LargeBinary, nullable=True)
    )
