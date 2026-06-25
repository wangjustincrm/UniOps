"""create invoice_po_allocations (multi-PO line-level allocation)

Revision ID: y1_invoice_po_allocations
Revises: x8_add_tax_code_snapshot
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "y1_invoice_po_allocations"
down_revision = "x8_add_tax_code_snapshot"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invoice_po_allocations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id", UUID(as_uuid=True),
                  sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_line_id", UUID(as_uuid=True), nullable=False),
        sa.Column("po_id", UUID(as_uuid=True),
                  sa.ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("po_line_id", UUID(as_uuid=True),
                  sa.ForeignKey("po_line_items.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("allocated_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("allocated_tax", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("allocated_total", sa.Numeric(15, 2), nullable=False),
        sa.Column("variance", sa.Numeric(15, 2), nullable=True),
        sa.Column("variance_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoice_po_allocations_invoice_id", "invoice_po_allocations", ["invoice_id"])
    op.create_index("ix_invoice_po_allocations_po_id", "invoice_po_allocations", ["po_id"])
    op.create_index("ix_invoice_po_allocations_po_line_id", "invoice_po_allocations", ["po_line_id"])


def downgrade():
    op.drop_index("ix_invoice_po_allocations_po_line_id", table_name="invoice_po_allocations")
    op.drop_index("ix_invoice_po_allocations_po_id", table_name="invoice_po_allocations")
    op.drop_index("ix_invoice_po_allocations_invoice_id", table_name="invoice_po_allocations")
    op.drop_table("invoice_po_allocations")
