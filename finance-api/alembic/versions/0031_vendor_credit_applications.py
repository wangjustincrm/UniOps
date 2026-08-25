"""vendor credit applications + payment_records.credit_applied — Phase B

Revision ID: 0031_vc_applications
Revises: 0030_vendor_credits
Create Date: 2026-08-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0031_vc_applications"
down_revision = "0030_vendor_credits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_credit_applications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("credit_id", UUID(as_uuid=True),
                  sa.ForeignKey("vendor_credits.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_record_id", UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", UUID(as_uuid=True), nullable=True),
        sa.Column("doc_kind", sa.String(20), nullable=False),
        sa.Column("doc_id", UUID(as_uuid=True), nullable=False),
        sa.Column("doc_number", sa.String(40), nullable=True),
        sa.Column("applied_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_by", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("applied_amount > 0", name="ck_vendor_credit_applications_positive"),
    )
    op.create_index("ix_vendor_credit_applications_credit_id", "vendor_credit_applications", ["credit_id"])
    op.create_index("ix_vendor_credit_applications_payment_record_id", "vendor_credit_applications", ["payment_record_id"])
    op.create_index("ix_vendor_credit_applications_doc_id", "vendor_credit_applications", ["doc_id"])

    op.add_column("payment_records",
                  sa.Column("credit_applied", sa.Numeric(15, 2), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("payment_records", "credit_applied")
    op.drop_table("vendor_credit_applications")
