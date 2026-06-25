"""Add host-specified PPE request to vms_visits.

The Host can opt-in to PPE staging when creating a visit. When they tick
the box, they pick the clothing size, footwear (shoes vs covers), and
shoe size if applicable. After the visit is approved (or immediately, if
no approval is needed) the Janitor contact gets an email with the
specifics so they can pre-stage gear before the visitor arrives.

Stored as JSONB so we can grow the schema (more sizes, extra notes) without
new migrations. `ppe_notified_at` is the idempotency flag — same pattern
as `host_notified_at`.

Revision ID: 20260602_0008
Revises: 20260602_0007
Create Date: 2026-06-02
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260602_0008"
down_revision = "20260602_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vms_visits", sa.Column("ppe_requested", JSONB(), nullable=True))
    op.add_column(
        "vms_visits",
        sa.Column("ppe_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vms_visits", "ppe_notified_at")
    op.drop_column("vms_visits", "ppe_requested")
