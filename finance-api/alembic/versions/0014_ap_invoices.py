"""create ap_invoices + ap_invoice_tax_lines (finance-owned AP invoice master)

Revision ID: 0014_ap_invoices
Revises: 0013_accounts_receivable
Create Date: 2026-06-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0014_ap_invoices"
down_revision = "0013_accounts_receivable"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ap_invoices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ap_invoice_number", sa.String(40), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("source_invoice_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_ref", sa.String(40), nullable=True),
        sa.Column("vendor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("vendor_name", sa.String(255), nullable=True),
        sa.Column("vendor_invoice_number", sa.String(100), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("paid_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("source_status", sa.String(30), nullable=True),
        sa.Column("po_id", UUID(as_uuid=True), nullable=True),
        sa.Column("po_number", sa.String(40), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ap_invoice_number", name="uq_ap_invoices_number"),
        sa.UniqueConstraint("source", "source_invoice_id", name="uq_ap_invoices_source"),
    )
    op.create_index("ix_ap_invoices_source", "ap_invoices", ["source"])
    op.create_index("ix_ap_invoices_vendor_id", "ap_invoices", ["vendor_id"])
    op.create_index("ix_ap_invoices_status", "ap_invoices", ["status"])
    op.create_index("ix_ap_invoices_due_date", "ap_invoices", ["due_date"])

    op.create_table(
        "ap_invoice_tax_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id", UUID(as_uuid=True),
                  sa.ForeignKey("ap_invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("tax_code", sa.String(20), nullable=True),
        sa.Column("taxable_base", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("recoverable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ap_invoice_tax_lines_invoice_id", "ap_invoice_tax_lines", ["invoice_id"])


def downgrade():
    op.drop_table("ap_invoice_tax_lines")
    op.drop_table("ap_invoices")
