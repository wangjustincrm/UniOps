"""seed line_role mapping for AP accrual (Phase a A2)

purchase_expense is the fallback debit account for vendor-invoice accruals
(invoices carry no account dimension yet); finance retargets it via the COA
mappings UI. 5000 = COGS — Materials in the IFRS seed.

Revision ID: 0007_purchase_expense
Revises: 0006_coa_aux
Create Date: 2026-06-12
"""
import uuid

from alembic import op
import sqlalchemy as sa

revision = "0007_purchase_expense"
down_revision = "0006_coa_aux"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text(
        "INSERT INTO account_mappings (id, mapping_type, source_code, account_code) "
        "VALUES (:id, 'line_role', 'purchase_expense', '5000') "
        "ON CONFLICT (mapping_type, source_code) DO NOTHING"
    ).bindparams(id=uuid.uuid4()))


def downgrade():
    op.execute("DELETE FROM account_mappings WHERE mapping_type='line_role' AND source_code='purchase_expense'")
