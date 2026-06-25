"""expense_line_items.tax_code — ITC groundwork (Phase 0-B2, FIN-EXP-008)

References mdm-api tax_codes by code string; no FK by design. Recoverability
is resolved from the tax code master at reporting time.

Revision ID: 0010_line_tax_code
Revises: 0009
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_line_tax_code"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("expense_line_items", sa.Column("tax_code", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("expense_line_items", "tax_code")
