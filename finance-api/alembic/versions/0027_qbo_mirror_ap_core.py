"""qbo mirror AP core tables

Revision ID: 0027_qbo_mirror_ap_core
Revises: 0026_jv_lines_nc_cc_code
Create Date: 2026-07-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0027_qbo_mirror_ap_core"
down_revision = "0026_jv_lines_nc_cc_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qbo_sync_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("mode", sa.String(15), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="running"),
        sa.Column("started_by", UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counters", JSONB, nullable=False, server_default="{}"),
        sa.Column("watermarks", JSONB, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_qbo_sync_runs_status", "qbo_sync_runs", ["status"])

    op.create_table(
        "qbo_accounts",
        sa.Column("qbo_id", sa.String(20), primary_key=True),
        sa.Column("sync_token", sa.String(10)),
        sa.Column("name", sa.String(255)),
        sa.Column("acct_num", sa.String(50)),
        sa.Column("account_type", sa.String(64)),
        sa.Column("account_sub_type", sa.String(64)),
        sa.Column("currency", sa.String(10)),
        sa.Column("current_balance", sa.Numeric(20, 2)),
        sa.Column("active", sa.Boolean),
        sa.Column("last_updated_time", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "qbo_vendors",
        sa.Column("qbo_id", sa.String(20), primary_key=True),
        sa.Column("sync_token", sa.String(10)),
        sa.Column("display_name", sa.String(255)),
        sa.Column("print_on_check_name", sa.String(255)),
        sa.Column("currency", sa.String(10)),
        sa.Column("balance", sa.Numeric(20, 2)),
        sa.Column("email", sa.String(255)),
        sa.Column("active", sa.Boolean),
        sa.Column("last_updated_time", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "qbo_bills",
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
    op.create_index("ix_qbo_bills_txn_date", "qbo_bills", ["txn_date"])
    op.create_index("ix_qbo_bills_counterparty_id", "qbo_bills", ["counterparty_id"])
    op.create_index("ix_qbo_bills_doc_number", "qbo_bills", ["doc_number"])
    op.create_table(
        "qbo_bill_lines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("parent_qbo_id", sa.String(20), nullable=False),
        sa.Column("line_num", sa.Integer),
        sa.Column("amount", sa.Numeric(20, 2)),
        sa.Column("detail_type", sa.String(48)),
        sa.Column("account_id", sa.String(20)),
        sa.Column("account_name", sa.String(255)),
        sa.Column("tax_code_ref", sa.String(20)),
        sa.Column("description", sa.Text),
        sa.Column("linked_txn_id", sa.String(20)),
        sa.Column("linked_txn_type", sa.String(32)),
        sa.Column("raw", JSONB, nullable=False),
    )
    op.create_index("ix_qbo_bill_lines_parent", "qbo_bill_lines", ["parent_qbo_id"])
    # Further entity tables are added in later steps of this same migration (later tasks).


def downgrade() -> None:
    op.drop_index("ix_qbo_bill_lines_parent", table_name="qbo_bill_lines")
    op.drop_table("qbo_bill_lines")
    op.drop_index("ix_qbo_bills_doc_number", table_name="qbo_bills")
    op.drop_index("ix_qbo_bills_counterparty_id", table_name="qbo_bills")
    op.drop_index("ix_qbo_bills_txn_date", table_name="qbo_bills")
    op.drop_table("qbo_bills")
    op.drop_table("qbo_vendors")
    op.drop_table("qbo_accounts")
    op.drop_index("ix_qbo_sync_runs_status", table_name="qbo_sync_runs")
    op.drop_table("qbo_sync_runs")
