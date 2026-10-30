"""agr01: agreement consumed_amount is pre-tax

Revision ID: agr01_consumed_pretax
Revises: gr02_damage_resolution
Create Date: 2026-10-30

ISS-0128: Not-to-Exceed and the milestone stage amounts are pre-tax, but
consumed_amount summed the matched invoices' tax-inclusive totals, so an
agreement invoiced exactly to its stages showed over its ceiling by the tax.
crud.invoice._recompute_consumed now sums Invoice.amount (pre-tax); this
re-derives the stored value for every agreement the same way. Data only:
consumed_amount is always recomputable from the matched invoices.
On the production snapshot of 2026-10-29: 15 agreements change, none has an
NTE, so no agreement crosses its ceiling either way.
"""
from alembic import op

revision = "agr01_consumed_pretax"
down_revision = "gr02_damage_resolution"
branch_labels = None
depends_on = None


def _recompute(column: str) -> None:
    op.execute(f"""
        UPDATE purchase_agreements a
           SET consumed_amount = COALESCE(
                 (SELECT SUM(i.{column}) FROM invoices i WHERE i.agreement_id = a.id), 0)
         WHERE a.consumed_amount IS DISTINCT FROM COALESCE(
                 (SELECT SUM(i.{column}) FROM invoices i WHERE i.agreement_id = a.id), 0)
    """)


def upgrade() -> None:
    _recompute("amount")


def downgrade() -> None:
    _recompute("total_amount")
