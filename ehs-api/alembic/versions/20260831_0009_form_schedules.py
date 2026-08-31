"""Scheduling inspections, so a form that exists also gets filled in.

The engine can render a checklist and raise actions from its failures, and none
of that happens if nobody is told to walk the racking this month. This is the
part that turns a template into a recurring obligation.

**Department rotation** is the shape HSE described, and it is not the obvious
one. Their JHSC inspects "one department per month", cycling through the list —
not every department every month, and not a random pick. So a schedule holds an
ordered list of departments and a cursor into it, and each occurrence advances
the cursor by one. The cursor is stored rather than derived from a count of past
runs, because a skipped or deleted run must not shift the rotation for every
month after it.

**Runs are the occurrences.** A schedule says "monthly"; a run says "December,
Packaging, due the 31st, nobody has done it". Separating them is what makes an
overdue inspection a thing with a status rather than an absence — you cannot
chase, count or report on something that was never written down.

A run points at the submission once someone starts filling it in. The submission
is deliberately NOT created up front: an unfilled inspection would otherwise
leave a trail of empty drafts that look like work in progress.

Revision ID: 20260831_0009
Revises: 20260831_0008
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260831_0009"
down_revision = "20260831_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_form_schedules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("template_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_form_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        # daily | weekly | monthly | quarterly | annual
        sa.Column("frequency", sa.String(20), nullable=False),
        # none | department — "one department per month, in turn" (HSE's JHSC).
        sa.Column("rotation", sa.String(20), nullable=False, server_default="none"),
        # Ordered rotation list, or the fixed areas this schedule covers.
        sa.Column("department_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("location_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        # Where the rotation is up to. Stored, not derived: deleting a run must
        # not shift every month after it.
        sa.Column("rotation_index", sa.Integer, nullable=False, server_default="0"),

        sa.Column("assigned_role", sa.String(50), nullable=True),
        sa.Column("assigned_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        # How many days before it is due the task should appear.
        sa.Column("lead_days", sa.Integer, nullable=False, server_default="7"),

        sa.Column("next_due_on", sa.Date, nullable=False),
        sa.Column("last_generated_on", sa.Date, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ehs_form_schedules_template", "ehs_form_schedules", ["template_id"])
    op.create_index("ix_ehs_form_schedules_due", "ehs_form_schedules",
                    ["is_active", "next_due_on"])

    op.create_table(
        "ehs_form_schedule_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("schedule_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_form_schedules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_form_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("due_on", sa.Date, nullable=False),
        # Which department's turn this occurrence was, with the name snapshotted
        # so a renamed or deleted department leaves the history readable.
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("department_name", sa.String(255), nullable=True),
        sa.Column("location_id", UUID(as_uuid=True), nullable=True),
        # pending | completed | missed
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("submission_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_form_submissions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        # One occurrence per schedule per due date. The sweep runs every few
        # minutes and re-reads the same schedules; without this a restart would
        # produce a second copy of every pending inspection.
        sa.UniqueConstraint("schedule_id", "due_on", name="uq_ehs_schedule_run_due"),
    )
    op.create_index("ix_ehs_form_schedule_runs_status", "ehs_form_schedule_runs",
                    ["status", "due_on"])


def downgrade() -> None:
    op.drop_table("ehs_form_schedule_runs")
    op.drop_table("ehs_form_schedules")
