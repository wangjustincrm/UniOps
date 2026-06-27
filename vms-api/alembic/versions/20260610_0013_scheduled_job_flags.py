"""Scheduled-job idempotency flags on vms_visits.

The background scheduler (app.services.scheduler) fires the day-before
reminder (VMS-PR-012), the 1h-overdue host reminder (VMS-CO-010), and the
4h-overdue dept-manager escalation (VMS-CO-011). Each tick re-runs every few
minutes, so we persist a one-shot timestamp per channel to avoid re-sending.
No-show (VMS-PR-019) needs no flag — it is a status transition.

All three columns are nullable timestamptz; existing rows default to NULL
(= "not yet sent"), which is correct.

Revision ID: 20260610_0013
Revises: 20260608_0012
Create Date: 2026-06-10
"""
import sqlalchemy as sa
from alembic import op

revision = "20260610_0013"
down_revision = "20260608_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vms_visits",
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vms_visits",
        sa.Column("overdue_reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vms_visits",
        sa.Column("overdue_escalated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vms_visits", "overdue_escalated_at")
    op.drop_column("vms_visits", "overdue_reminder_sent_at")
    op.drop_column("vms_visits", "reminder_sent_at")
