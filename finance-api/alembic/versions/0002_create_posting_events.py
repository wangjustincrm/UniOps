"""create posting_events and posting_lines

Revision ID: 0002_posting_events
Revises: 0001_payment_records
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_posting_events"
down_revision = "0001_payment_records"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "posting_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_service", sa.String(20), nullable=False),
        sa.Column("source_doc_type", sa.String(30), nullable=False),
        sa.Column("source_doc_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_doc_number", sa.String(40), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_doc_type", "source_doc_id", "event_type",
                            name="uq_posting_events_source"),
    )
    op.create_index("ix_posting_events_doc", "posting_events",
                    ["source_doc_type", "source_doc_id"])

    op.create_table(
        "posting_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", UUID(as_uuid=True),
                  sa.ForeignKey("posting_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer, nullable=False),
        sa.Column("line_role", sa.String(30), nullable=False),
        sa.Column("account_code", sa.String(20), nullable=True),
        sa.Column("cost_center_id", UUID(as_uuid=True), nullable=True),
        sa.Column("partner_id", UUID(as_uuid=True), nullable=True),
        sa.Column("partner_name", sa.String(255), nullable=True),
        sa.Column("debit", sa.Numeric(15, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("credit", sa.Numeric(15, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("tax_code", sa.String(20), nullable=True),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("fx_rate", sa.Numeric(12, 6), nullable=False, server_default=sa.text("1")),
        sa.Column("memo", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("NOT (debit > 0 AND credit > 0)", name="ck_posting_lines_one_side"),
    )
    op.create_index("ix_posting_lines_event_id", "posting_lines", ["event_id"])


def downgrade():
    op.drop_table("posting_lines")
    op.drop_table("posting_events")
