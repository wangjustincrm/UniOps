"""Continuous demand series + append-only change log (Task 1, plan doc
2026-08-06-continuous-sales-forecast). Models: app/models/demand_series.py.
Also adds `source_anchor_month` to mrp_forecast_versions — the window
anchor a snapshot was frozen from (later task freezes ForecastVersion as
the "outlook snapshot" of MrpDemandSeries).

Revision ID: mrp05_demand_series
Revises: mrp04_capacity_mps_demand
Create Date: 2026-08-06
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "mrp05_demand_series"
down_revision = "mrp04_capacity_mps_demand"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mrp_demand_series",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("material_code", sa.String(50), nullable=False),
        sa.Column("month", sa.CHAR(7), nullable=False),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("uom", sa.String(10), nullable=False, server_default="KG"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("material_code", "month", name="uq_mrp_demand_series_material_month"),
    )
    op.create_index("ix_mrp_demand_series_material_code", "mrp_demand_series", ["material_code"])
    op.create_index("ix_mrp_demand_series_month", "mrp_demand_series", ["month"])

    op.create_table(
        "mrp_forecast_change_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("material_code", sa.String(50), nullable=False),
        sa.Column("month", sa.CHAR(7), nullable=False),
        sa.Column("old_qty", sa.Numeric(18, 3), nullable=True),
        sa.Column("new_qty", sa.Numeric(18, 3), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("changed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mrp_forecast_change_log_material_code", "mrp_forecast_change_log", ["material_code"])
    op.create_index("ix_mrp_forecast_change_log_month", "mrp_forecast_change_log", ["month"])

    op.add_column(
        "mrp_forecast_versions",
        sa.Column("source_anchor_month", sa.CHAR(length=7), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mrp_forecast_versions", "source_anchor_month")
    op.drop_table("mrp_forecast_change_log")
    op.drop_table("mrp_demand_series")
