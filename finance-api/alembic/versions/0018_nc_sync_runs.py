"""nc_sync_runs — NC65 voucher sync audit/watermark/progress log

Revision ID: 0018_nc_sync_runs
Revises: 0017_journal_vouchers
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0018_nc_sync_runs"
down_revision = "0017_journal_vouchers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nc_sync_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("mode", sa.String(15), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("started_by", UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watermark_from", sa.String(19), nullable=True),
        sa.Column("watermark_to", sa.String(19), nullable=True),
        sa.Column("vouchers_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vouchers_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dims_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmapped_cc_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_nc_sync_runs_status", "nc_sync_runs", ["status"])


def downgrade():
    op.drop_index("ix_nc_sync_runs_status", table_name="nc_sync_runs")
    op.drop_table("nc_sync_runs")
