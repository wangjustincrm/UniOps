"""payment_remittance_notifications — remittance advice send log

Revision ID: 0027_remittance_notifications
Revises: 0026_jv_lines_nc_cc_code
Create Date: 2026-07-22

NOTE: the table name (payment_remittance_notifications) is unchanged; only
the alembic revision id/filename is shortened. This repo's
alembic_version_finance table uses Alembic's default version_num
VARCHAR(32) (see alembic/env.py — only version_table is customized, not
version_table_column_size). The originally-specified id
"0027_payment_remittance_notifications" is 37 chars and overflows that
column (StringDataRightTruncationError), so it's shortened here to stay
consistent with every other revision id in this chain (all <=25 chars).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0027_remittance_notifications"
down_revision = "0026_jv_lines_nc_cc_code"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_remittance_notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_kind", sa.String(10), nullable=False),
        sa.Column("scope_id", UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_kind", sa.String(10), nullable=False),
        sa.Column("party_id", UUID(as_uuid=True), nullable=False),
        sa.Column("party_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("payment_record_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("scope_kind", "scope_id", "recipient_kind", "party_id",
                            name="uq_remittance_scope_party"),
    )
    op.create_index("ix_remittance_scope", "payment_remittance_notifications",
                    ["scope_kind", "scope_id"])
    # Backs the hub's "was this payment notified?" containment query.
    op.create_index("ix_remittance_record_ids", "payment_remittance_notifications",
                    ["payment_record_ids"], postgresql_using="gin")


def downgrade():
    op.drop_index("ix_remittance_record_ids", table_name="payment_remittance_notifications")
    op.drop_index("ix_remittance_scope", table_name="payment_remittance_notifications")
    op.drop_table("payment_remittance_notifications")
