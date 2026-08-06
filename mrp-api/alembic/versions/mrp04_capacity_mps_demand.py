"""Capacity rules (Task 1). Task 2 adds three more op.create_table calls to
this same migration for the MPS/demand tables (plan doc
2026-08-06-mrp-phase1b-mps-capacity) — keep the revision id/filename as-is
when that lands.

Revision ID: mrp04_capacity_mps_demand
Revises: mrp03_consignment_uom
Create Date: 2026-08-06
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "mrp04_capacity_mps_demand"
down_revision = "mrp03_consignment_uom"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mrp_capacity_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_ref", sa.String(50), nullable=True),
        sa.Column("constraint_type", sa.String(20), nullable=False),
        sa.Column("limit_value", sa.Numeric(18, 3), nullable=False),
        sa.Column("uom", sa.String(10), nullable=True),
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("effective_to", sa.Date, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mrp_capacity_rules")
