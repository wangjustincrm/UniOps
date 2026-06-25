"""Add host_notified_at column for one-shot approval-result emails (W12 / S2-D).

vms-api emails the Host once when their visit's approval state turns
terminal (approved → confirmed, or rejected/cancelled). To avoid double-sends
on subsequent reads, we persist the timestamp here.

Revision ID: 20260602_0004
Revises: 20260601_0003
Create Date: 2026-06-02
"""
import sqlalchemy as sa
from alembic import op

revision = "20260602_0004"
down_revision = "20260601_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vms_visits",
        sa.Column("host_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vms_visits", "host_notified_at")
