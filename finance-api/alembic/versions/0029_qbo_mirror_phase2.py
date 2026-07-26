"""qbo mirror phase 2 — AR, more transactions, long-tail raw, attachments

Revision ID: 0029_qbo_mirror_phase2
Revises: 0028_qbo_mirror_ap_core
Create Date: 2026-07-25

Re-chained during the 2026-07-26 reconcile with main (QBO migrations moved from
0027/0028 to 0028/0029 to sit after main's 0027_remittance_notifications).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0029_qbo_mirror_phase2"
down_revision = "0028_qbo_mirror_ap_core"
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

    op.create_table(
        "qbo_purchases",
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
        sa.Column("payment_type", sa.String(20)),
        sa.Column("is_credit", sa.Boolean),
        sa.Column("account_id", sa.String(20)),
        sa.Column("account_name", sa.String(255)),
        sa.Column("entity_id", sa.String(20)),
        sa.Column("entity_name", sa.String(255)),
        sa.Column("entity_type", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_qbo_purchases_txn_date", "qbo_purchases", ["txn_date"])
    op.create_table(
        "qbo_purchase_lines",
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
    op.create_index("ix_qbo_purchase_lines_parent", "qbo_purchase_lines", ["parent_qbo_id"])

    op.create_table(
        "qbo_deposits",
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
    op.create_index("ix_qbo_deposits_txn_date", "qbo_deposits", ["txn_date"])
    op.create_table(
        "qbo_deposit_lines",
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
    op.create_index("ix_qbo_deposit_lines_parent", "qbo_deposit_lines", ["parent_qbo_id"])

    op.create_table(
        "qbo_transfers",
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
        sa.Column("from_account_id", sa.String(20)),
        sa.Column("from_account_name", sa.String(255)),
        sa.Column("to_account_id", sa.String(20)),
        sa.Column("to_account_name", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_qbo_transfers_txn_date", "qbo_transfers", ["txn_date"])

    op.create_table(
        "qbo_journal_entries",
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
    op.create_index("ix_qbo_journal_entries_txn_date", "qbo_journal_entries", ["txn_date"])
    op.create_index("ix_qbo_journal_entries_doc_number", "qbo_journal_entries", ["doc_number"])
    op.create_table(
        "qbo_journal_entry_lines",
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
    op.create_index("ix_qbo_journal_entry_lines_parent", "qbo_journal_entry_lines", ["parent_qbo_id"])

    op.create_table(
        "qbo_raw",
        sa.Column("entity_type", sa.String(40), primary_key=True),
        sa.Column("qbo_id", sa.String(20), primary_key=True),
        sa.Column("last_updated_time", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "qbo_attachments",
        sa.Column("qbo_id", sa.String(20), primary_key=True),
        sa.Column("file_name", sa.String(512)),
        sa.Column("content_type", sa.String(128)),
        sa.Column("size", sa.Integer()),
        sa.Column("content", sa.LargeBinary()),
        sa.Column("last_updated_time", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "qbo_attachment_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("attachment_qbo_id", sa.String(20), nullable=False),
        sa.Column("txn_id", sa.String(20), nullable=False),
        sa.Column("txn_type", sa.String(32)),
    )
    op.create_index("ix_qbo_attachment_links_attachment", "qbo_attachment_links", ["attachment_qbo_id"])


def downgrade() -> None:
    op.drop_table("qbo_raw")

    op.drop_index("ix_qbo_journal_entry_lines_parent", table_name="qbo_journal_entry_lines")
    op.drop_table("qbo_journal_entry_lines")
    op.drop_index("ix_qbo_journal_entries_doc_number", table_name="qbo_journal_entries")
    op.drop_index("ix_qbo_journal_entries_txn_date", table_name="qbo_journal_entries")
    op.drop_table("qbo_journal_entries")

    op.drop_index("ix_qbo_transfers_txn_date", table_name="qbo_transfers")
    op.drop_table("qbo_transfers")

    op.drop_index("ix_qbo_deposit_lines_parent", table_name="qbo_deposit_lines")
    op.drop_table("qbo_deposit_lines")
    op.drop_index("ix_qbo_deposits_txn_date", table_name="qbo_deposits")
    op.drop_table("qbo_deposits")

    op.drop_index("ix_qbo_purchase_lines_parent", table_name="qbo_purchase_lines")
    op.drop_table("qbo_purchase_lines")
    op.drop_index("ix_qbo_purchases_txn_date", table_name="qbo_purchases")
    op.drop_table("qbo_purchases")

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

    op.drop_index("ix_qbo_attachment_links_attachment", table_name="qbo_attachment_links")
    op.drop_table("qbo_attachment_links")
    op.drop_table("qbo_attachments")

    for t in _EXISTING_LINE_TABLES:
        op.drop_column(t, "posting_type")
