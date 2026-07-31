"""add receipt_override columns to payment_applications

Revision ID: af_add_receipt_override_to_pa
Revises: ae_add_approved_at_to_pa
Create Date: 2026-07-30
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "af_add_receipt_override_to_pa"
down_revision = "ae_add_approved_at_to_pa"   # 实测唯一 head(2026-07-30)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_applications",
        sa.Column("receipt_override", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("payment_applications",
        sa.Column("receipt_override_reason", sa.Text(), nullable=True))
    op.add_column("payment_applications",
        sa.Column("receipt_override_by", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_applications", "receipt_override_by")
    op.drop_column("payment_applications", "receipt_override_reason")
    op.drop_column("payment_applications", "receipt_override")
