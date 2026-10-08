"""Let finance retire a stale NC payable from the working list.

NC's subledger carries balances nobody can clear any more. Justin's example
(2026-09-23): McMaster-Carr D12020091700126990 and ...127033, both 2020, both
1,028.18, both never paid and never cleared — NC went live around then, the
periods are closed at month-end AND year-end, and the documents can no longer
be touched in NC. They are not debt and they are not work; they are noise
sitting on top of every supplier this page asks finance to judge.

There is nowhere in NC to record that judgement, so it is recorded here. The
rules this table exists to enforce:

  * It NEVER changes what NC says. `money_bal` is still read from the mirror
    and still reported. A dismissal is a second, clearly-labelled figure —
    "ignored as legacy" — subtracted in the open, so a number on this page and
    a number from an NC report can always be reconciled.
  * It is per DOCUMENT, not per supplier: a supplier with one stale 2020 bill
    and a live 2026 one must lose the first and keep the second.
  * It is reversible and it keeps its history. `restored_at` retires a
    dismissal instead of deleting it, so "who ignored 1,028.18 of payable, when
    and why" survives someone changing their mind. The unique index is partial
    on that column, which is also what allows a bill to be dismissed again.
  * It records the balance AS IT STOOD. If a later NC sync moves the bill, the
    difference between `money_bal_at_dismissal` and today's figure is visible
    rather than silently absorbed.

Revision ID: 0035_ap_bill_dismiss
Revises: 0034_nc_ap_payments
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0035_ap_bill_dismiss"
down_revision = "0034_nc_ap_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nc_ap_bill_dismissals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        # NC's document number, not our mirror's row id: the mirror is rebuilt
        # by every full reload and its UUIDs do not survive that. The bill
        # number does.
        sa.Column("bill_no", sa.String(40), nullable=False),
        sa.Column("supplier_code", sa.String(50), nullable=True),
        sa.Column("supplier_name", sa.String(200), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("money_bal_at_dismissal", sa.Numeric(18, 2), nullable=True),
        sa.Column("bill_date", sa.Date(), nullable=True),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("dismissed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("dismissed_by_name", sa.String(200), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_by", UUID(as_uuid=True), nullable=True),
        sa.Column("restored_by_name", sa.String(200), nullable=True),
    )
    # Partial: one LIVE dismissal per bill, any number of retired ones behind
    # it. A plain unique constraint would make a restored bill undismissable.
    op.create_index("ux_nc_ap_dismissal_live", "nc_ap_bill_dismissals", ["bill_no"],
                    unique=True, postgresql_where=sa.text("restored_at is null"))
    op.create_index("ix_nc_ap_dismissal_supplier", "nc_ap_bill_dismissals",
                    ["supplier_code", "currency"])


def downgrade() -> None:
    op.drop_index("ix_nc_ap_dismissal_supplier", table_name="nc_ap_bill_dismissals")
    op.drop_index("ux_nc_ap_dismissal_live", table_name="nc_ap_bill_dismissals")
    op.drop_table("nc_ap_bill_dismissals")
