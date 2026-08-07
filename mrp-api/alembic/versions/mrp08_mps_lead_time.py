"""Production lead time (Production Lead Time task 2, see
.superpowers/sdd/2026-08-07-mps-production-lead-time/task-2-brief.md).

`app/services/mps_engine.py::generate_mps` (task 1) gained a required
`lead_months` param that shifts a line's default placement earlier than its
demand month, plus a `PlannedLine.lead_shortfall` flag that is true when the
lead couldn't be fully honoured (the target was clamped to `current_month`).
This migration adds the run-level input (`production_lead_months`, fed
straight into `lead_months`) and the line-level output flag
(`lead_shortfall`) so both survive past a single request/response cycle.

`production_lead_months` defaults to 1 (NOT NULL) so pre-existing runs (and
any caller that omits it) plan one month ahead rather than silently
reproducing `lead_months=0` (same-month) behaviour. `lead_shortfall` defaults
to `false` -- pre-existing lines were generated before this concept existed,
so "no shortfall" is the only defensible backfill value; regenerate/
recalculate a run to get a real value.

Revision ID: mrp08_mps_lead_time
Revises: mrp07_mps_line_demand_context
Create Date: 2026-08-07
"""
from alembic import op
import sqlalchemy as sa

revision = "mrp08_mps_lead_time"
down_revision = "mrp07_mps_line_demand_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mrp_mps_runs",
        sa.Column("production_lead_months", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "mrp_mps_lines",
        sa.Column("lead_shortfall", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("mrp_mps_lines", "lead_shortfall")
    op.drop_column("mrp_mps_runs", "production_lead_months")
