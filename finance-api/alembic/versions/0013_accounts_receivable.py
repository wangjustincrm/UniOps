"""accounts receivable: ar_invoices + ar_invoice_tax_lines + ar_receipts
   + line_role mappings (accounts_receivable / revenue / output_tax) (Phase c)

AR control / revenue / GST/HST payable accounts already exist in the IFRS seed
(1100 / 4000 / 2200); this just bridges the posting line roles to them.

Revision ID: 0013_accounts_receivable
Revises: 0012_bank_import_mapping
Create Date: 2026-06-16
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0013_accounts_receivable"
down_revision = "0012_bank_import_mapping"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ar_invoices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_number", sa.String(40), nullable=False, unique=True),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("paid_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("invoice_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft", index=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "ar_invoice_tax_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id", UUID(as_uuid=True),
                  sa.ForeignKey("ar_invoices.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("line_no", sa.Integer, nullable=False),
        sa.Column("tax_code", sa.String(20), nullable=False),
        sa.Column("taxable_base", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "ar_receipts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("receipt_number", sa.String(40), nullable=False, unique=True),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("invoice_id", UUID(as_uuid=True),
                  sa.ForeignKey("ar_invoices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("receipt_date", sa.Date, nullable=False),
        sa.Column("method", sa.String(20), nullable=False, server_default="bank_transfer"),
        sa.Column("bank_account_id", UUID(as_uuid=True), nullable=True),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("recorded_by", UUID(as_uuid=True), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # bridge AR posting line roles to the existing IFRS accounts
    mappings = sa.table(
        "account_mappings",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("mapping_type", sa.String),
        sa.column("source_code", sa.String),
        sa.column("account_code", sa.String),
    )
    op.bulk_insert(mappings, [
        {"id": uuid.uuid4(), "mapping_type": "line_role", "source_code": "accounts_receivable", "account_code": "1100"},
        {"id": uuid.uuid4(), "mapping_type": "line_role", "source_code": "revenue", "account_code": "4000"},
        {"id": uuid.uuid4(), "mapping_type": "line_role", "source_code": "output_tax", "account_code": "2200"},
    ])


def downgrade():
    op.execute("DELETE FROM account_mappings WHERE mapping_type='line_role' "
               "AND source_code IN ('accounts_receivable','revenue','output_tax')")
    op.drop_table("ar_receipts")
    op.drop_table("ar_invoice_tax_lines")
    op.drop_table("ar_invoices")
