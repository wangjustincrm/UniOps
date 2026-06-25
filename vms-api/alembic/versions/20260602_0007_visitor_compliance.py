"""Add per-visitor training + PPE compliance timestamps.

Operators wanted training (HR-run safety orientation) and PPE issuance to
be tracked at the **visitor** level, not per-visit — frequent suppliers
who come in monthly shouldn't re-train every visit. The freshness window
is 12 months (decision lives in code, not schema, so it can move without
a migration).

A visit's badge print blocks for GMP / Lab visitors whose record is
missing or older than 12 months; visit creation creates Task rows for
the configured HR / Janitor emails to confirm.

Revision ID: 20260602_0007
Revises: 20260602_0006
Create Date: 2026-06-02
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260602_0007"
down_revision = "20260602_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vms_visitors",
        sa.Column("safety_training_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vms_visitors",
        sa.Column("safety_training_confirmed_by", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "vms_visitors",
        sa.Column("ppe_issued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vms_visitors",
        sa.Column("ppe_issued_by", UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vms_visitors", "ppe_issued_by")
    op.drop_column("vms_visitors", "ppe_issued_at")
    op.drop_column("vms_visitors", "safety_training_confirmed_by")
    op.drop_column("vms_visitors", "safety_training_confirmed_at")
