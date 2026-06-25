"""expense_invoice_lines.unit — UOM code per line (OA Direct PA)

Free string referencing mdm-api units_of_measure by code; no FK by design.

Revision ID: 0011_invoice_line_unit
Revises: 0010_line_tax_code
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_invoice_line_unit"
down_revision = "0010_line_tax_code"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("expense_invoice_lines", sa.Column("unit", sa.String(50), nullable=True))


def downgrade():
    op.drop_column("expense_invoice_lines", "unit")
