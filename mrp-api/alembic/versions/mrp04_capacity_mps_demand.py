"""Capacity rules (Task 1) + MPS run/line + mrp_demands (Task 2, plan doc
2026-08-06-mrp-phase1b-mps-capacity). Models: app/models/capacity.py,
app/models/mps.py, app/models/demand.py.

Revision ID: mrp04_capacity_mps_demand
Revises: mrp03_consignment_uom
Create Date: 2026-08-06
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

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

    op.create_table(
        "mrp_mps_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("run_no", sa.String(50), nullable=False),
        sa.Column("forecast_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("horizon_start_month", sa.CHAR(7), nullable=False),
        sa.Column("horizon_months", sa.Integer, nullable=False, server_default="18"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("safety_margin_fraction", sa.Numeric(6, 4), nullable=False, server_default="0"),
        sa.Column("generated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("stats", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_no", name="uq_mrp_mps_runs_run_no"),
    )
    op.create_index("ix_mrp_mps_runs_run_no", "mrp_mps_runs", ["run_no"])
    op.create_index("ix_mrp_mps_runs_forecast_version_id", "mrp_mps_runs", ["forecast_version_id"])

    op.create_table(
        "mrp_mps_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("mrp_mps_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("material_code", sa.String(50), nullable=False),
        sa.Column("demand_month", sa.CHAR(7), nullable=False),
        sa.Column("plan_month", sa.CHAR(7), nullable=False),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("is_prebuild", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("prebuild_reason", sa.Text, nullable=True),
        sa.Column("shelf_life_ok", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("capacity_gap", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("locked_by_planner", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("manual_adjusted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mrp_mps_lines_run_id", "mrp_mps_lines", ["run_id"])
    op.create_index("ix_mrp_mps_lines_material_code", "mrp_mps_lines", ["material_code"])

    op.create_table(
        "mrp_demands",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("source_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("demand_type", sa.String(20), nullable=False, server_default="mps"),
        sa.Column("material_code", sa.String(50), nullable=False),
        sa.Column("demand_month", sa.CHAR(7), nullable=False),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mrp_demands_source_run_id", "mrp_demands", ["source_run_id"])
    op.create_index("ix_mrp_demands_material_code", "mrp_demands", ["material_code"])
    op.create_index("ix_mrp_demands_demand_month", "mrp_demands", ["demand_month"])


def downgrade() -> None:
    op.drop_table("mrp_demands")
    op.drop_table("mrp_mps_lines")
    op.drop_table("mrp_mps_runs")
    op.drop_table("mrp_capacity_rules")
