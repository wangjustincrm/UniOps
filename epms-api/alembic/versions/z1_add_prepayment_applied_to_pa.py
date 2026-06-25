"""add prepayment_applied to payment_applications

Revision ID: z1_add_prepayment_applied
Revises: y1_invoice_po_allocations
"""
import sqlalchemy as sa
from alembic import op

revision = "z1_add_prepayment_applied"
down_revision = "y1_invoice_po_allocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_applications",
        sa.Column("prepayment_applied", sa.Numeric(15, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_applications", "prepayment_applied")
