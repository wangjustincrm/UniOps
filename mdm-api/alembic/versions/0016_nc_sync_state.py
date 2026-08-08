"""nc_sync_state: sync status/watermark tracking for NC-sourced canonical
syncs (MRP phase1a Task 7) — starts with one row, source='nc_bom', written
by `POST /mdm/v1/boms/sync` on both success and failure so the BOM Explorer
UI can show "Last synced: … (N hours ago)" (design spec §6.6's "Sync 按钮"
section flags this as a pre-existing gap: the sync had no state tracking at
all before this migration). See app/models/sync_state.py's docstring for why
this is a new table rather than an extension of `erp_sync_state`.

Revision ID: 0016_nc_sync_state
Revises: 0015_bom_qty_normalize
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016_nc_sync_state"
down_revision = "0015_bom_qty_normalize"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nc_sync_state",
        sa.Column("source", sa.String(20), primary_key=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_stats", postgresql.JSONB(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("nc_sync_state")
