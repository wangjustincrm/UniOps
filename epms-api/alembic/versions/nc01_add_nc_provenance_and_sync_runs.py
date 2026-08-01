"""add NC provenance columns + nc_purchase_sync_runs

Revision ID: nc01_nc_provenance
Revises: af_add_receipt_override_to_pa
Create Date: 2026-08-01
"""
import sqlalchemy as sa
from alembic import op

revision = "nc01_nc_provenance"
down_revision = "af_add_receipt_override_to_pa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for tbl in ("purchase_orders", "goods_receipts"):
        op.add_column(tbl, sa.Column("source", sa.String(length=10), nullable=True))
        op.add_column(tbl, sa.Column("nc_source_pk", sa.String(length=20), nullable=True))
        op.create_index(f"ix_{tbl}_source", tbl, ["source"])
        op.create_index(
            f"uq_{tbl}_nc_source_pk", tbl, ["nc_source_pk"],
            unique=True, postgresql_where=sa.text("source = 'nc'"),
        )
    for tbl in ("po_line_items", "gr_line_items"):
        op.add_column(tbl, sa.Column("nc_source_pk", sa.String(length=20), nullable=True))
        op.create_index(
            f"uq_{tbl}_nc_source_pk", tbl, ["nc_source_pk"],
            unique=True, postgresql_where=sa.text("nc_source_pk IS NOT NULL"),
        )

    op.create_table(
        "nc_purchase_sync_runs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("mode", sa.String(length=15), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="running"),
        sa.Column("started_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watermark_from", sa.String(length=19), nullable=True),
        sa.Column("watermark_to", sa.String(length=19), nullable=True),
        sa.Column("pos_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("po_lines_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("grs_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gr_lines_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_no_vendor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_consumed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_nc_purchase_sync_runs_status", "nc_purchase_sync_runs", ["status"])


def downgrade() -> None:
    op.drop_table("nc_purchase_sync_runs")
    for tbl in ("po_line_items", "gr_line_items"):
        op.drop_index(f"uq_{tbl}_nc_source_pk", table_name=tbl)
        op.drop_column(tbl, "nc_source_pk")
    for tbl in ("purchase_orders", "goods_receipts"):
        op.drop_index(f"uq_{tbl}_nc_source_pk", table_name=tbl)
        op.drop_index(f"ix_{tbl}_source", table_name=tbl)
        op.drop_column(tbl, "nc_source_pk")
        op.drop_column(tbl, "source")
