"""Drop NOT NULL on vms_visitors.phone — phone is now optional at intake.

Operators wanted to register walk-in visitors without typing in a phone
number (most badge holders are already known to Reception by company +
name). Phone stays a string column but the constraint relaxes to nullable.

Revision ID: 20260602_0005
Revises: 20260602_0004
Create Date: 2026-06-02
"""
import sqlalchemy as sa
from alembic import op

revision = "20260602_0005"
down_revision = "20260602_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("vms_visitors", "phone", existing_type=sa.String(20), nullable=True)


def downgrade() -> None:
    # Downgrade only safe if no NULL rows exist; assume callers handle that
    # out-of-band (e.g. backfill empty string) before reverting.
    op.alter_column("vms_visitors", "phone", existing_type=sa.String(20), nullable=False)
