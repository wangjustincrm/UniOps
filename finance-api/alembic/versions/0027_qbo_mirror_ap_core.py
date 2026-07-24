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
    # Further entity tables are added in later steps of this same migration (later tasks).


def downgrade() -> None:
    op.drop_table("qbo_vendors")
    op.drop_table("qbo_accounts")
    op.drop_index("ix_qbo_sync_runs_status", table_name="qbo_sync_runs")
    op.drop_table("qbo_sync_runs")
