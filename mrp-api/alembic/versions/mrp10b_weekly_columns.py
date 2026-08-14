"""Weekly MPS/demand columns (Task 6 of the 2026-08-12 weekly-planning plan).

*** DESTRUCTIVE MIGRATION ***

This migration wipes every existing MPS run, MPS line, and materialized
demand row (Decision D9). Monthly plans cannot be mechanically converted to
weekly buckets: doing so would mean this migration *invents* a plan week for
history that has already been released/confirmed, silently writing numbers
nobody actually planned. The business owner reviewed and approved deleting
that data rather than fabricating it.

`downgrade()` below restores the old monthly column shape so the ORM models
and any code that still expects it can run again -- but it does **not**
restore the deleted rows. There is no way to reconstruct which monthly plan
week a given (now-gone) run/line/demand belonged to. Anyone running
`downgrade` is rolling back the schema, not the data: mrp_mps_runs,
mrp_mps_lines, and mrp_demands come back empty.

Standing capacity rules (`mrp_capacity_rules`) are deactivated
(`is_active = false`), not deleted: their `limit_value`s were entered as
monthly ceilings. Leaving them active would have the new weekly engine read
a monthly number as a weekly one -- roughly 4x the real capacity, with no
error or warning. Deactivating keeps the old values on screen for a planner
to review and re-enter per week; `downgrade()` does not reactivate them,
since a rollback can't tell which rules a planner has since edited by hand.

Column changes:
- mrp_mps_runs.production_lead_months -> production_lead_weeks (renamed;
  same Integer column, new server_default "4" -- four weeks replaces the
  old one-month default so pre-existing/omitted runs keep planning roughly
  the same distance ahead).
- mrp_mps_runs: + week_calendar_mode (String(20), NOT NULL, default
  "iso_thursday" -- matches mrp10a's mrp_planning_params default and
  app/services/week_calendar.py's WEEK_MODES[0]).
- mrp_mps_lines.plan_month -> dropped, replaced by:
    + plan_week_start (Date, NOT NULL) -- the Monday/ISO-week start the line
      is scheduled in.
    + plan_week_month (CHAR(7), NOT NULL) -- the calendar month
      plan_week_start falls in, denormalized so month-level rollups (Task 7's
      API, existing reports) don't need a date computation on every read.
    + weeks_early (Integer, NOT NULL, default 0) -- weekly analogue of the
      monthly lead-time shortfall bookkeeping (mrp08's lead_shortfall stays
      as-is; this is additive, how many weeks ahead of the demand week the
      line was actually placed).
- mrp_demands.plan_week_start (Date, NOT NULL) -- weekly analogue of
  demand_month for material-explosion consumers (Phase 1C).

The `ADD COLUMN ... NOT NULL` calls below have no server_default (except
weeks_early/week_calendar_mode, which are legitimately defaultable). That
only works because the three DELETEs above run first and empty the tables --
do not reorder this migration, and do not add a default to make the ordering
"safer": a default would silently invent a plan week for rows that should
not exist.

Revision ID: mrp10b_weekly_columns
Revises: mrp10a_planning_params
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa

revision = "mrp10b_weekly_columns"
down_revision = "mrp10a_planning_params"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Decision D9: monthly plans are wiped rather than converted -- converting
    # would write system-invented numbers into already-released history.
    # Order matters: mrp_demands references mrp_mps_runs (source_run_id) and
    # mrp_mps_lines FKs to mrp_mps_runs too, so children go first.
    op.execute("DELETE FROM mrp_demands")
    op.execute("DELETE FROM mrp_mps_lines")
    op.execute("DELETE FROM mrp_mps_runs")
    # Standing rules keep their monthly VALUES; leaving them active would
    # silently read a monthly ceiling as a weekly one (~4x the real capacity).
    op.execute("UPDATE mrp_capacity_rules SET is_active = false")

    op.alter_column(
        "mrp_mps_runs", "production_lead_months",
        new_column_name="production_lead_weeks",
        server_default="4",
    )
    op.add_column(
        "mrp_mps_runs",
        sa.Column("week_calendar_mode", sa.String(20), nullable=False,
                  server_default="iso_thursday"),
    )

    op.drop_column("mrp_mps_lines", "plan_month")
    op.add_column("mrp_mps_lines", sa.Column("plan_week_start", sa.Date(), nullable=False))
    op.add_column("mrp_mps_lines", sa.Column("plan_week_month", sa.CHAR(7), nullable=False))
    op.add_column(
        "mrp_mps_lines",
        sa.Column("weeks_early", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_mrp_mps_lines_plan_week_start", "mrp_mps_lines", ["plan_week_start"])

    op.add_column("mrp_demands", sa.Column("plan_week_start", sa.Date(), nullable=False))
    op.create_index("ix_mrp_demands_plan_week_start", "mrp_demands", ["plan_week_start"])


def downgrade() -> None:
    """Restores the monthly column shape only. The rows deleted by upgrade()
    (every mrp_mps_run, mrp_mps_line, mrp_demand as of this migration) are
    gone permanently -- this is a structural rollback, not a data recovery."""
    op.drop_index("ix_mrp_demands_plan_week_start", table_name="mrp_demands")
    op.drop_column("mrp_demands", "plan_week_start")

    op.drop_index("ix_mrp_mps_lines_plan_week_start", table_name="mrp_mps_lines")
    op.drop_column("mrp_mps_lines", "weeks_early")
    op.drop_column("mrp_mps_lines", "plan_week_month")
    op.drop_column("mrp_mps_lines", "plan_week_start")
    # NOT NULL with no server_default: only safe because mrp_mps_lines is
    # still empty here (upgrade() wiped it and downgrade() doesn't restore
    # data). Run this downgrade later, once Task 7 has shipped and real
    # weekly rows exist, and this add_column hard-fails on the NOT NULL
    # constraint -- a loud, safe failure, not the silent-invention mode the
    # upgrade side warns about, so left as-is. If that path ever matters,
    # plan_week_month already holds each row's calendar month, so
    # `plan_month = plan_week_month` would be a valid backfill to run before
    # this add_column -- not implemented here since downgrade never runs
    # against non-empty tables today.
    op.add_column("mrp_mps_lines", sa.Column("plan_month", sa.CHAR(7), nullable=False))

    op.drop_column("mrp_mps_runs", "week_calendar_mode")
    op.alter_column(
        "mrp_mps_runs", "production_lead_weeks",
        new_column_name="production_lead_months",
        server_default="1",
    )

    # Standing capacity rules that upgrade() deactivated are intentionally
    # NOT reactivated here: a rollback can't tell which of them a planner
    # has since edited by hand, so re-flipping is_active=true would be a
    # guess, not a restore.
