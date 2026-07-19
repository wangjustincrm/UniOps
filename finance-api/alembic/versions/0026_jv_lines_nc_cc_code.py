"""journal_voucher_lines.nc_cc_code — store raw NC cost-center code

Revision ID: 0026_jv_lines_nc_cc_code
Revises: 0025_budget_actual_cc_map
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_jv_lines_nc_cc_code"
down_revision = "0025_budget_actual_cc_map"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("journal_voucher_lines",
                  sa.Column("nc_cc_code", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("journal_voucher_lines", "nc_cc_code")
