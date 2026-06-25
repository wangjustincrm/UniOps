"""payment_batches + payment_batch_lines + payment_records.batch_id (Phase a A4)

Revision ID: 0011_payment_batches
Revises: 0010_aux_catalog
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0011_payment_batches"
down_revision = "0010_aux_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_number", sa.String(30), nullable=False, unique=True),
        sa.Column("batch_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("total", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("payment_method", sa.String(20), nullable=False, server_default="bank_transfer"),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_payment_batches_status", "payment_batches", ["status"])

    op.create_table(
        "payment_batch_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", UUID(as_uuid=True),
                  sa.ForeignKey("payment_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_kind", sa.String(20), nullable=False),
        sa.Column("doc_id", UUID(as_uuid=True), nullable=False),
        sa.Column("doc_number", sa.String(40), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("payment_record_id", UUID(as_uuid=True), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_payment_batch_lines_batch", "payment_batch_lines", ["batch_id"])

    op.add_column("payment_records", sa.Column("batch_id", UUID(as_uuid=True), nullable=True))


def downgrade():
    op.drop_column("payment_records", "batch_id")
    op.drop_table("payment_batch_lines")
    op.drop_table("payment_batches")
