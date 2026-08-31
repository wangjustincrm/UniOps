"""Two figures HSE's dashboard shows and the incident record never held.

Their management dashboard has an "Incident Cost" tile with a running total and
an average, and a "Total Lost Time Hours" tile. Neither is derivable: cost is
what the company decides an incident cost it, and lost hours is what payroll
records, not what a duration between two timestamps would suggest.

`lost_time_hours` is hours, not days, because that is what their tile reads and
because a shift is not a day — a worker sent home four hours into an eight-hour
shift lost four hours, and rounding that to "1 day" or "0 days" is wrong in both
directions.

Both nullable and both filled in after the fact: neither is known when a report
is filed, which is the moment most of this record is written.

Revision ID: 20260831_0014
Revises: 20260831_0013
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op

revision = "20260831_0014"
down_revision = "20260831_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_incidents", sa.Column("cost", sa.Numeric(12, 2), nullable=True))
    op.add_column("ehs_incidents",
                  sa.Column("lost_time_hours", sa.Numeric(8, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_incidents", "lost_time_hours")
    op.drop_column("ehs_incidents", "cost")
