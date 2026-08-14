"""Minimum lot size, week start day and frozen-zone columns.

Purely additive, every column NOT NULL with a server default, so this runs
against a populated production database without rewriting a single existing
row's meaning:

- `mrp_mps_runs.week_start_dow` defaults to 0 (Monday), which is the grid
  every pre-existing run was actually planned on — those runs therefore
  replay byte-identically after the factory switches to Saturday-start
  weeks. Snapshotting it on the run (rather than reading the current
  planning parameter) is the same rule `week_calendar_mode` and
  `production_lead_weeks` already follow.
- `mrp_mps_runs.frozen_months` defaults to 3, the frozen-zone length the
  plant confirmed (materials for those months are already purchased).
- The five/six line columns are engine output: quantity rounded up to a
  minimum lot size (`surplus_qty`), surplus consumed from an earlier
  month (`carry_in_qty`), and four flags the matrix renders as badges.

Revision chain: mrp10b_weekly_columns -> mrp11_min_lot_week_start_frozen.
"""
import sqlalchemy as sa
from alembic import op

revision = "mrp11_min_lot_week_start_frozen"
down_revision = "mrp10b_weekly_columns"
branch_labels = None
depends_on = None

_LINE_NUMERIC = ("surplus_qty", "carry_in_qty")
_LINE_FLAGS = ("late_production", "surplus_expiry_risk", "below_min_lot", "covered_by_carry")


def upgrade() -> None:
    for name in _LINE_NUMERIC:
        op.add_column(
            "mrp_mps_lines",
            sa.Column(name, sa.Numeric(18, 3), nullable=False, server_default="0"),
        )
    for name in _LINE_FLAGS:
        op.add_column(
            "mrp_mps_lines",
            sa.Column(name, sa.Boolean(), nullable=False, server_default="false"),
        )
    op.add_column(
        "mrp_mps_runs",
        sa.Column("week_start_dow", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "mrp_mps_runs",
        sa.Column("frozen_months", sa.SmallInteger(), nullable=False, server_default="3"),
    )


def downgrade() -> None:
    op.drop_column("mrp_mps_runs", "frozen_months")
    op.drop_column("mrp_mps_runs", "week_start_dow")
    for name in reversed(_LINE_FLAGS):
        op.drop_column("mrp_mps_lines", name)
    for name in reversed(_LINE_NUMERIC):
        op.drop_column("mrp_mps_lines", name)
