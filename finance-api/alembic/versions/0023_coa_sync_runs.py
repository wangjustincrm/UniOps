"""coa_sync_runs + coa_aux_items.required — NC COA/aux sync audit & NC parity

Revision ID: 0023_coa_sync_runs
Revises: 0022_ap_vendor_invno_unique
Create Date: 2026-07-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0023_coa_sync_runs"
down_revision = "0022_ap_vendor_invno_unique"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "coa_sync_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("started_by", UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accounts_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accounts_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accounts_deactivated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("aux_items_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("aux_items_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    # NC BD_ACCASS.ISEMPTY inverted — never populated before (spec §2.3)
    op.add_column("coa_aux_items",
                  sa.Column("required", sa.Boolean(), nullable=False,
                            server_default="false"))


def downgrade():
    op.drop_column("coa_aux_items", "required")
    op.drop_table("coa_sync_runs")
