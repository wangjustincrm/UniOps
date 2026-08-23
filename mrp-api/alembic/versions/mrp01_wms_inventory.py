"""Flux WMS inventory lot mirror + status mapping + sync state (Task 8)

Chain head for mrp-api's own `alembic_version_mrp` table (empty chain before
this — down_revision=None).

Revision ID: mrp01
Revises:
Create Date: 2026-08-04
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "mrp01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wms_inventory_lots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("warehouse_id", sa.String(20), nullable=False, index=True),
        sa.Column("material_code", sa.String(50), nullable=False, index=True),
        sa.Column("lot_no", sa.String(50), nullable=False, index=True),
        sa.Column("qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("qty_allocated", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("qty_onhold", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("wms_status", sa.String(10), nullable=True),
        sa.Column("mapped_status", sa.String(20), nullable=False, index=True),
        sa.Column("production_date", sa.Date, nullable=True),
        sa.Column("expiry_date", sa.Date, nullable=True, index=True),
        sa.Column("inbound_date", sa.Date, nullable=True),
        sa.Column("supplier_batch", sa.String(100), nullable=True),
        sa.Column("supplier_code", sa.String(50), nullable=True),
        sa.Column("source_doc", sa.String(100), nullable=True),
        sa.Column("wms_edit_time", sa.DateTime(timezone=False), nullable=True),
        sa.Column("sync_batch_id", sa.String(50), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("warehouse_id", "material_code", "lot_no", name="uq_wms_inventory_lots_wh_mat_lot"),
    )

    mapping = op.create_table(
        "mrp_status_mapping",
        sa.Column("wms_code", sa.String(10), primary_key=True),
        sa.Column("mapped_status", sa.String(20), nullable=False),
        sa.Column("description", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "mrp_sync_state",
        sa.Column("source", sa.String(20), primary_key=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # QLT_STS dictionary, surveyed live against Flux WMS (design doc appendix
    # A): 01=Block, 02=Release, 04=Under Inspection. 'expired' is never seeded
    # here — it is always derived at transform time from expiry_date < today.
    op.bulk_insert(mapping, [
        {"wms_code": "01", "mapped_status": "hold", "description": "Block"},
        {"wms_code": "02", "mapped_status": "available", "description": "Release"},
        {"wms_code": "04", "mapped_status": "hold", "description": "Under Inspection"},
    ])


def downgrade() -> None:
    op.drop_table("mrp_sync_state")
    op.drop_table("mrp_status_mapping")
    op.drop_table("wms_inventory_lots")
