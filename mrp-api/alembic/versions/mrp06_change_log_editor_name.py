"""Add mrp_forecast_change_log.changed_by_name (Continuous Sales Forecast
follow-up — the change-history popover must show the editor's NAME, not
their UUID). See app/models/demand_series.py's MrpForecastChangeLog.changed_by_name
docstring for why this is write-time denormalization rather than a live
lookup at read time.

Revision ID: mrp06_change_log_editor_name
Revises: mrp05_demand_series
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision = "mrp06_change_log_editor_name"
down_revision = "mrp05_demand_series"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mrp_forecast_change_log",
        sa.Column("changed_by_name", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mrp_forecast_change_log", "changed_by_name")
