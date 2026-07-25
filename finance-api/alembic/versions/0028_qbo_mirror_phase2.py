"""qbo mirror phase 2 — AR, more transactions, long-tail raw, attachments

Revision ID: 0028_qbo_mirror_phase2
Revises: 0027_qbo_mirror_ap_core
Create Date: 2026-07-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0028_qbo_mirror_phase2"
down_revision = "0027_qbo_mirror_ap_core"
branch_labels = None
depends_on = None

# Phase-1 line tables that gain posting_type.
_EXISTING_LINE_TABLES = ("qbo_bill_lines", "qbo_bill_payment_lines", "qbo_vendor_credit_lines")


def upgrade() -> None:
    for t in _EXISTING_LINE_TABLES:
        op.add_column(t, sa.Column("posting_type", sa.String(10)))

    op.create_table(
        "qbo_invoices",
        sa.Column("qbo_id", sa.String(20), primary_key=True),
        sa.Column("sync_token", sa.String(10)),
        sa.Column("doc_number", sa.String(64)),
        sa.Column("txn_date", sa.String(10)),
        sa.Column("due_date", sa.String(10)),
        sa.Column("currency", sa.String(10)),
        sa.Column("exchange_rate", sa.Numeric(20, 8)),
        sa.Column("total_amt", sa.Numeric(20, 2)),
        sa.Column("home_total_amt", sa.Numeric(20, 2)),
        sa.Column("balance", sa.Numeric(20, 2)),
        sa.Column("home_balance", sa.Numeric(20, 2)),
        sa.Column("global_tax_calc", sa.String(20)),
        sa.Column("private_note", sa.Text),
        sa.Column("counterparty_id", sa.String(20)),
        sa.Column("counterparty_name", sa.String(255)),
        sa.Column("last_updated_time", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_qbo_invoices_txn_date", "qbo_invoices", ["txn_date"])
    op.create_index("ix_qbo_invoices_counterparty_id", "qbo_invoices", ["counterparty_id"])
    op.create_index("ix_qbo_invoices_doc_number", "qbo_invoices", ["doc_number"])
    op.create_table(
        "qbo_invoice_lines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("parent_qbo_id", sa.String(20), nullable=False),
        sa.Column("line_num", sa.Integer),
        sa.Column("amount", sa.Numeric(20, 2)),
        sa.Column("detail_type", sa.String(48)),
        sa.Column("account_id", sa.String(20)),
        sa.Column("account_name", sa.String(255)),
        sa.Column("tax_code_ref", sa.String(20)),
        sa.Column("posting_type", sa.String(10)),
        sa.Column("description", sa.Text),
        sa.Column("linked_txn_id", sa.String(20)),
        sa.Column("linked_txn_type", sa.String(32)),
        sa.Column("raw", JSONB, nullable=False),
    )
    op.create_index("ix_qbo_invoice_lines_parent", "qbo_invoice_lines", ["parent_qbo_id"])

    op.create_table(
        "qbo_payments",
        sa.Column("qbo_id", sa.String(20), primary_key=True),
        sa.Column("sync_token", sa.String(10)),
        sa.Column("doc_number", sa.String(64)),
        sa.Column("txn_date", sa.String(10)),
        sa.Column("due_date", sa.String(10)),
        sa.Column("currency", sa.String(10)),
        sa.Column("exchange_rate", sa.Numeric(20, 8)),
        sa.Column("total_amt", sa.Numeric(20, 2)),
        sa.Column("home_total_amt", sa.Numeric(20, 2)),
        sa.Column("balance", sa.Numeric(20, 2)),
        sa.Column("home_balance", sa.Numeric(20, 2)),
        sa.Column("global_tax_calc", sa.String(20)),
        sa.Column("private_note", sa.Text),
        sa.Column("counterparty_id", sa.String(20)),
        sa.Column("counterparty_name", sa.String(255)),
        sa.Column("last_updated_time", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("deposit_to_account_id", sa.String(20)),
        sa.Column("deposit_to_account_name", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_qbo_payments_txn_date", "qbo_payments", ["txn_date"])
    op.create_index("ix_qbo_payments_counterparty_id", "qbo_payments", ["counterparty_id"])
    op.create_table(
        "qbo_payment_lines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("parent_qbo_id", sa.String(20), nullable=False),
        sa.Column("line_num", sa.Integer),
        sa.Column("amount", sa.Numeric(20, 2)),
        sa.Column("detail_type", sa.String(48)),
        sa.Column("account_id", sa.String(20)),
        sa.Column("account_name", sa.String(255)),
        sa.Column("tax_code_ref", sa.String(20)),
        sa.Column("posting_type", sa.String(10)),
        sa.Column("description", sa.Text),
        sa.Column("linked_txn_id", sa.String(20)),
        sa.Column("linked_txn_type", sa.String(32)),
        sa.Column("raw", JSONB, nullable=False),
    )
    op.create_index("ix_qbo_payment_lines_parent", "qbo_payment_lines", ["parent_qbo_id"])

    op.create_table(
        "qbo_credit_memos",
        sa.Column("qbo_id", sa.String(20), primary_key=True),
        sa.Column("sync_token", sa.String(10)),
        sa.Column("doc_number", sa.String(64)),
        sa.Column("txn_date", sa.String(10)),
        sa.Column("due_date", sa.String(10)),
        sa.Column("currency", sa.String(10)),
        sa.Column("exchange_rate", sa.Numeric(20, 8)),
        sa.Column("total_amt", sa.Numeric(20, 2)),
        sa.Column("home_total_amt", sa.Numeric(20, 2)),
        sa.Column("balance", sa.Numeric(20, 2)),
        sa.Column("home_balance", sa.Numeric(20, 2)),
        sa.Column("global_tax_calc", sa.String(20)),
        sa.Column("private_note", sa.Text),
        sa.Column("counterparty_id", sa.String(20)),
        sa.Column("counterparty_name", sa.String(255)),
        sa.Column("last_updated_time", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_qbo_credit_memos_txn_date", "qbo_credit_memos", ["txn_date"])
    op.create_index("ix_qbo_credit_memos_counterparty_id", "qbo_credit_memos", ["counterparty_id"])
    op.create_index("ix_qbo_credit_memos_doc_number", "qbo_credit_memos", ["doc_number"])
    op.create_table(
        "qbo_credit_memo_lines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("parent_qbo_id", sa.String(20), nullable=False),
        sa.Column("line_num", sa.Integer),
        sa.Column("amount", sa.Numeric(20, 2)),
        sa.Column("detail_type", sa.String(48)),
        sa.Column("account_id", sa.String(20)),
        sa.Column("account_name", sa.String(255)),
        sa.Column("tax_code_ref", sa.String(20)),
        sa.Column("posting_type", sa.String(10)),
        sa.Column("description", sa.Text),
        sa.Column("linked_txn_id", sa.String(20)),
        sa.Column("linked_txn_type", sa.String(32)),
        sa.Column("raw", JSONB, nullable=False),
    )
    op.create_index("ix_qbo_credit_memo_lines_parent", "qbo_credit_memo_lines", ["parent_qbo_id"])
    # New tables are added by later steps of this migration (later Phase-2 tasks).


def downgrade() -> None:
    op.drop_index("ix_qbo_credit_memo_lines_parent", table_name="qbo_credit_memo_lines")
    op.drop_table("qbo_credit_memo_lines")
    op.drop_index("ix_qbo_credit_memos_doc_number", table_name="qbo_credit_memos")
    op.drop_index("ix_qbo_credit_memos_counterparty_id", table_name="qbo_credit_memos")
    op.drop_index("ix_qbo_credit_memos_txn_date", table_name="qbo_credit_memos")
    op.drop_table("qbo_credit_memos")

    op.drop_index("ix_qbo_payment_lines_parent", table_name="qbo_payment_lines")
    op.drop_table("qbo_payment_lines")
    op.drop_index("ix_qbo_payments_counterparty_id", table_name="qbo_payments")
    op.drop_index("ix_qbo_payments_txn_date", table_name="qbo_payments")
    op.drop_table("qbo_payments")

    op.drop_index("ix_qbo_invoice_lines_parent", table_name="qbo_invoice_lines")
    op.drop_table("qbo_invoice_lines")
    op.drop_index("ix_qbo_invoices_doc_number", table_name="qbo_invoices")
    op.drop_index("ix_qbo_invoices_counterparty_id", table_name="qbo_invoices")
    op.drop_index("ix_qbo_invoices_txn_date", table_name="qbo_invoices")
    op.drop_table("qbo_invoices")

    for t in _EXISTING_LINE_TABLES:
        op.drop_column(t, "posting_type")
