"""tasks.notified_at / notify_vars: one notification outbox for every task

Revision ID: tn01_task_notified_at
Revises: as02_assistant_saved_q
Create Date: 2026-10-08

Approval emails used to be sent by whichever endpoint happened to drive the
approval engine, so only PR / PO ever got them — PA, agreements, expense
claims, budget plans and VMS visits never did. Tasks are written by four
services into this one table; a NULL notified_at is the one signal they all
already produce, so the notifier in epms-api picks up every new task and
applies the admin's per-task-type policy (Portal → Admin → Notifications).

Every existing row is stamped as handled: the policy is for tasks created from
now on, and an un-stamped backlog would mail thousands of old tasks the minute
the notifier starts.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "tn01_task_notified_at"
down_revision = "as02_assistant_saved_q"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("notify_vars", JSONB, nullable=True))
    op.execute("UPDATE tasks SET notified_at = created_at WHERE notified_at IS NULL")
    # The notifier polls for the (few) un-handled rows; keep that a tiny index.
    op.create_index(
        "ix_tasks_notify_pending", "tasks", ["created_at"],
        postgresql_where=sa.text("notified_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_notify_pending", table_name="tasks")
    op.drop_column("tasks", "notify_vars")
    op.drop_column("tasks", "notified_at")
