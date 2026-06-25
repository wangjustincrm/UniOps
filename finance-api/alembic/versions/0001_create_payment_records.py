"""create payment_records table

Revision ID: 0001_payment_records
Revises:
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
import uuid

revision = "0001_payment_records"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("pa_id", UUID(as_uuid=True), sa.ForeignKey("payment_applications.id"), nullable=False, index=True),
        sa.Column("pa_number", sa.String(40), nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_name", sa.String(255), nullable=False),
        sa.Column("payment_date", sa.Date, nullable=False),
        sa.Column("payment_method", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("recorded_by", UUID(as_uuid=True), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="completed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("payment_records")
