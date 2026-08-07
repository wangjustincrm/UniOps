"""create purchase_agreements

Revision ID: ag01_purchase_agreements
Revises: nc02_nc_cutover
Create Date: 2026-08-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ag01_purchase_agreements"
down_revision = "nc02_nc_cutover"   # 实测唯一 head(2026-08-06,53 revisions)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_agreements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("agreement_type", sa.String(20), nullable=False),
        sa.Column("contract_no", sa.String(100), nullable=True),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("business_partners.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("vendor_name", sa.String(255), nullable=False),
        sa.Column("vendor_reference", sa.String(100), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("grace_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("not_to_exceed", sa.Numeric(15, 2), nullable=True),
        sa.Column("consumed_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("tax_code", sa.String(20), nullable=True),
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("budget_code", sa.String(100), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("approval_step_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_purchase_agreements_number", "purchase_agreements", ["number"], unique=True)
    op.create_index("ix_purchase_agreements_vendor_id", "purchase_agreements", ["vendor_id"])
    op.create_index("ix_purchase_agreements_vendor_reference", "purchase_agreements", ["vendor_reference"])
    op.create_index("ix_purchase_agreements_status", "purchase_agreements", ["status"])
    op.create_index("ix_purchase_agreements_department_id", "purchase_agreements", ["department_id"])
    op.create_index("ix_purchase_agreements_created_by", "purchase_agreements", ["created_by"])


def downgrade() -> None:
    op.drop_table("purchase_agreements")
