"""Certificate expiry notice rules and the log that sends each one once.

Round 8 (open box): a rule emails a specified address a set number of days
before training certificates expire. See app/models/expiry_notice.py.

Revision ID: 20261020_0071
Revises: 20261020_0070
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261020_0071"
down_revision = "20261020_0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_cert_expiry_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("days_before", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "ehs_cert_expiry_notices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("rule_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_cert_expiry_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("rule_id", "source_type", "source_id", "expires_on",
                            name="uq_ehs_cert_expiry_notice"),
    )
    op.create_index("ix_ehs_cert_expiry_notices_rule_id", "ehs_cert_expiry_notices", ["rule_id"])


def downgrade() -> None:
    op.drop_index("ix_ehs_cert_expiry_notices_rule_id", table_name="ehs_cert_expiry_notices")
    op.drop_table("ehs_cert_expiry_notices")
    op.drop_table("ehs_cert_expiry_rules")
