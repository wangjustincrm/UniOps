"""OA S3 revision — drop file_data BYTEA, OCR is now client-side.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('expense_invoices', 'file_data')
    # Add ocr_raw as JSONB if not already present (noop if column exists)
    # file_name / file_mime_type / file_size_bytes kept for metadata display


def downgrade() -> None:
    op.add_column('expense_invoices',
        sa.Column('file_data', sa.LargeBinary, nullable=True))
