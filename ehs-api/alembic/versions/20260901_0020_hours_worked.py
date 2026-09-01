"""Total hours worked, one figure per month.

HSE, replying to PRD v0.4 §4.1: "The HSE officer will enter the total hours
worked manually in the settings page. one entry per month and the numbers will
be editable in that is they can be changed after the fact in case there is a
correction that needs to be made."

Until now `hours_worked` was a query parameter on the dashboard and the reports
— somebody had to know the number and type it in every time they looked, and
nothing was kept. So the injury rates the whole safety programme is judged on
were, in practice, blank.

One row per month rather than a running total, because that is what makes
"which month is missing" answerable. A rate computed across a period with a gap
is not a slightly-low rate, it is a wrong one, and the reports now say which
months they are missing rather than dividing by whatever happened to be there.

Revision ID: 20260901_0020
Revises: 20260831_0019
Create Date: 2026-09-01
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260901_0020"
down_revision = "20260831_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_hours_worked",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("year", sa.SmallInteger, nullable=False, index=True),
        sa.Column("month", sa.SmallInteger, nullable=False),
        # Numeric because payroll reports part hours; rounding a plant's
        # monthly total to whole hours moves the third decimal of the rate.
        sa.Column("hours", sa.Numeric(12, 2), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("entered_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("entered_by_name", sa.String(255), nullable=True),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("month >= 1 AND month <= 12", name="ck_ehs_hours_month"),
        sa.CheckConstraint("hours >= 0", name="ck_ehs_hours_nonneg"),
        sa.UniqueConstraint("year", "month", name="uq_ehs_hours_worked_month"),
    )


def downgrade() -> None:
    op.drop_table("ehs_hours_worked")
