"""create journal_vouchers + journal_voucher_lines + jv_line_dimensions

Revision ID: 0017_journal_vouchers
Revises: 0016_payment_account_kind
Create Date: 2026-07-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0017_journal_vouchers"
down_revision = "0016_payment_account_kind"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "journal_vouchers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("jv_number", sa.String(40), nullable=False),
        sa.Column("voucher_word", sa.String(10), nullable=False, server_default="JV"),
        sa.Column("voucher_date", sa.Date(), nullable=False),
        sa.Column("fiscal_period", sa.String(7), nullable=False),
        sa.Column("summary", sa.String(255), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("posting_event_id", UUID(as_uuid=True),
                  sa.ForeignKey("posting_events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_service", sa.String(20), nullable=True),
        sa.Column("source_doc_type", sa.String(30), nullable=True),
        sa.Column("source_doc_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_doc_number", sa.String(40), nullable=True),
        sa.Column("prepared_by", UUID(as_uuid=True), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_by", UUID(as_uuid=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverses_jv_id", UUID(as_uuid=True), nullable=True),
        sa.Column("reversed_by_jv_id", UUID(as_uuid=True), nullable=True),
        sa.Column("total_debit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_credit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_local_debit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_local_credit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("nc_source_pk", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("posting_event_id", name="uq_journal_vouchers_event"),
        sa.CheckConstraint("status in ('draft','reviewed','posted','reversed')",
                           name="ck_journal_vouchers_status"),
    )
    op.create_index("ix_journal_vouchers_period", "journal_vouchers", ["fiscal_period"])
    op.create_index("ix_journal_vouchers_status", "journal_vouchers", ["status"])
    op.create_index("ix_journal_vouchers_doc", "journal_vouchers",
                    ["source_doc_type", "source_doc_id"])

    op.create_table(
        "journal_voucher_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("jv_id", UUID(as_uuid=True),
                  sa.ForeignKey("journal_vouchers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("account_code", sa.String(40), nullable=True),
        sa.Column("summary", sa.String(255), nullable=True),
        sa.Column("orig_debit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("orig_credit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("local_debit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("local_credit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("fx_rate", sa.Numeric(12, 6), nullable=False, server_default="1"),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=True),
        sa.Column("unit", sa.String(30), nullable=True),
        sa.Column("price", sa.Numeric(18, 6), nullable=True),
        sa.Column("cost_center_id", UUID(as_uuid=True), nullable=True),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("partner_id", UUID(as_uuid=True), nullable=True),
        sa.Column("partner_name", sa.String(255), nullable=True),
        sa.Column("tax_code", sa.String(20), nullable=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("item_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("NOT (orig_debit > 0 AND orig_credit > 0)",
                           name="ck_jv_lines_one_side"),
    )
    op.create_index("ix_jv_lines_jv_id", "journal_voucher_lines", ["jv_id"])
    op.create_index("ix_jv_lines_account", "journal_voucher_lines", ["account_code"])

    op.create_table(
        "jv_line_dimensions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("jv_line_id", UUID(as_uuid=True),
                  sa.ForeignKey("journal_voucher_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dim_code", sa.String(40), nullable=False),
        sa.Column("value_id", UUID(as_uuid=True), nullable=True),
        sa.Column("value_text", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_jv_line_dimensions_line", "jv_line_dimensions", ["jv_line_id"])


def downgrade():
    op.drop_table("jv_line_dimensions")
    op.drop_table("journal_voucher_lines")
    op.drop_table("journal_vouchers")
