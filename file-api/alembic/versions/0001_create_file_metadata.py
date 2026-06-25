"""create file_metadata table

Revision ID: 0001_file_metadata
Revises:
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0001_file_metadata"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "file_metadata",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=True), nullable=False),
        sa.Column("service", sa.String(50), nullable=False),
        sa.Column("doc_type", sa.String(20), nullable=False),
        sa.Column("doc_id", UUID(as_uuid=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_file_metadata_doc_id", "file_metadata", ["doc_id"])
    op.create_index("ix_file_metadata_service_doc", "file_metadata", ["service", "doc_type", "doc_id"])


def downgrade():
    op.drop_table("file_metadata")
