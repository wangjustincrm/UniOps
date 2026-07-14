"""nc_export_batches + ap_invoices export markers

Revision ID: 0021_nc_export_batches
Revises: 0020_nc_customers
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0021_nc_export_batches"
down_revision = "0020_nc_customers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nc_export_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("exported_by", UUID(as_uuid=True), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filename", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.add_column("ap_invoices",
                  sa.Column("nc_exported_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ap_invoices",
                  sa.Column("nc_export_batch_id", UUID(as_uuid=True), nullable=True))


def downgrade():
    op.drop_column("ap_invoices", "nc_export_batch_id")
    op.drop_column("ap_invoices", "nc_exported_at")
    op.drop_table("nc_export_batches")
