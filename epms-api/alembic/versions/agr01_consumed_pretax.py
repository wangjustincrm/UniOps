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

Before rewriting, the value of every agreement that changes is copied to
_bak_agr01_consumed_amount (id, number, the old tax-inclusive figure, when),
so a changed figure can be explained and put back exactly — recomputing from
today's invoices would not restore a value that had drifted. Downgrade puts
those values back and drops the table; without it, it recomputes as before.
"""
import sqlalchemy as sa
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


BACKUP_TABLE = "_bak_agr01_consumed_amount"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} AS
        SELECT a.id, a.number, a.consumed_amount AS consumed_amount_before,
               now() AS backed_up_at
          FROM purchase_agreements a
         WHERE a.consumed_amount IS DISTINCT FROM COALESCE(
                 (SELECT SUM(i.amount) FROM invoices i WHERE i.agreement_id = a.id), 0)
    """)
    _recompute("amount")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": BACKUP_TABLE}).scalar() is None:
        _recompute("total_amount")
        return
    op.execute(f"""
        UPDATE purchase_agreements a
           SET consumed_amount = b.consumed_amount_before
          FROM {BACKUP_TABLE} b
         WHERE b.id = a.id
    """)
    op.execute(f"DROP TABLE {BACKUP_TABLE}")
