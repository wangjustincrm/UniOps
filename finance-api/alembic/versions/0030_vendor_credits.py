"""vendor credits — Phase A ledger (record + review)

Revision ID: 0030_vendor_credits
Revises: 0029_qbo_mirror_phase2
Create Date: 2026-08-06

All vendor_credits columns land here, including the source/opening-balance
columns that only Phase C populates: the partial unique index below keys off
`source`, so the column must exist now. vendor_credit_applications and
payment_records.credit_applied belong to Phase B.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0030_vendor_credits"
down_revision = "0029_qbo_mirror_phase2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_credits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("credit_number", sa.String(30), nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_name", sa.String(255), nullable=False),
        sa.Column("vendor_credit_number", sa.String(100), nullable=False),
        sa.Column("credit_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("applied_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("remaining_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending_review"),
        sa.Column("po_id", UUID(as_uuid=True), nullable=True),
        sa.Column("po_number", sa.String(40), nullable=True),
        sa.Column("line_items", JSONB, nullable=False, server_default="[]"),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="upload"),
        sa.Column("source_ref", sa.String(20), nullable=True),
        sa.Column("opening_balance", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("imported_from_sync_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_by", UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by_name", sa.String(255), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_by_name", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("total_amount > 0", name="ck_vendor_credits_total_positive"),
        sa.CheckConstraint("applied_amount >= 0 AND remaining_amount >= 0",
                           name="ck_vendor_credits_nonneg"),
        sa.CheckConstraint("applied_amount + remaining_amount = total_amount",
                           name="ck_vendor_credits_balance"),
    )
    op.create_index("ix_vendor_credits_credit_number", "vendor_credits", ["credit_number"], unique=True)
    op.create_index("ix_vendor_credits_vendor_id", "vendor_credits", ["vendor_id"])
    op.create_index("ix_vendor_credits_status", "vendor_credits", ["status"])
    op.create_index(
        "uq_vendor_credits_vendor_docno", "vendor_credits",
        ["vendor_id", "vendor_credit_number"], unique=True,
        # Deliberately NOT scoped by source. One vendor document is one row,
        # whatever brought it in: if AP uploads a credit note manually and the
        # Phase C QBO import later pulls the same document, a source-scoped
        # predicate would let both rows live and silently double the vendor's
        # available credit — with both rows looking legitimate to drift
        # detection. The status <> 'void' carve-out stays: a rejected credit
        # must still be re-uploadable under the same document number.
        postgresql_where=sa.text("status <> 'void'"),
    )
    op.create_index(
        "uq_vendor_credits_source_ref", "vendor_credits",
        ["source", "source_ref"], unique=True,
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )
    op.create_index(
        "ix_vendor_credits_available", "vendor_credits",
        ["vendor_id", "currency", "credit_date"],
        postgresql_where=sa.text("status = 'available' AND remaining_amount > 0"),
    )


def downgrade() -> None:
    op.drop_table("vendor_credits")
