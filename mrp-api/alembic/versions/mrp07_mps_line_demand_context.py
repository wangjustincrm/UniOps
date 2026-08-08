"""Snapshot demand context onto mrp_mps_lines (Production Plan Matrix final
review fix). `demand_forecast` (gross forecast for the line's demand_month)
and `opening_stock` (rolled-forward PAB entering that demand_month) were
previously recomputed on every read from LIVE inventory -- a RELEASED
(immutable, point-in-time) run's "Available" would silently drift as stock
moved after generation, breaking the Demand - Available = Planned reading,
and every read paid the full net-requirement rollforward cost for every
forecast material. These are now written once at generate/recalculate time
and read back verbatim. See app/api/v1/mps.py's module docstring / this
task's final-fix-report.md for the full rationale.

Nullable: pre-mrp07 lines have NULL here -> read side falls back to 0 (see
mps.py's _line_response); no data backfill, regenerate the run to populate.

Revision ID: mrp07_mps_line_demand_context
Revises: mrp06_change_log_editor_name
Create Date: 2026-08-07
"""
from alembic import op
import sqlalchemy as sa

revision = "mrp07_mps_line_demand_context"
down_revision = "mrp06_change_log_editor_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mrp_mps_lines",
        sa.Column("demand_forecast", sa.Numeric(18, 3), nullable=True),
    )
    op.add_column(
        "mrp_mps_lines",
        sa.Column("opening_stock", sa.Numeric(18, 3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mrp_mps_lines", "opening_stock")
    op.drop_column("mrp_mps_lines", "demand_forecast")
