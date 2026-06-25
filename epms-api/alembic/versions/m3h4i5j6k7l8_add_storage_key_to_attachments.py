"""add storage_key to attachment tables and make file_data nullable

Revision ID: m3h4i5j6k7l8
Revises: l2g3h4i5j6k7
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "m3h4i5j6k7l8"
down_revision = "l2g3h4i5j6k7"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("pr_attachments", "pa_attachments", "gr_attachments"):
        op.add_column(table, sa.Column("storage_key", UUID(as_uuid=True), nullable=True))
        op.alter_column(table, "file_data", nullable=True)


def downgrade():
    for table in ("pr_attachments", "pa_attachments", "gr_attachments"):
        op.drop_column(table, "storage_key")
        op.alter_column(table, "file_data", nullable=False)
