"""create invoice_tax_lines — line-level tax split (Phase 0-B2)

Revision ID: x4_invoice_tax_lines
Revises: w3r4s5t6u7v8
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "x4_invoice_tax_lines"
down_revision = "w3r4s5t6u7v8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invoice_tax_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id", UUID(as_uuid=True),
                  sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer, nullable=False),
        sa.Column("tax_code", sa.String(20), nullable=False),
        sa.Column("taxable_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("recoverable", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoice_tax_lines_invoice_id", "invoice_tax_lines", ["invoice_id"])


def downgrade():
    op.drop_table("invoice_tax_lines")
