"""A delivery note or a service sign-off carries no money.

Raised by the user while testing: the entry form asked for Amount, Tax and
Total on all three receipt types. For a counter slip those are the point — the
slip IS a priced document and the invoice reconciles against it. A delivery
note or a work-order sign-off is a confirmation that goods or a service
arrived; there is no figure printed on it to key in, and asking for one
produces a number somebody invented.

The three columns become NULLABLE rather than defaulting to 0, because 0 is a
VALUE. A receipt storing 0.00 is summed as 0.00 by the invoice-side
reconciliation panel, which would then show a full-invoice "difference" and
ask the operator to explain it — a false alarm manufactured out of a field
that was never meant to have a number in it. NULL says what is true: this
receipt does not carry an amount, so it is not part of the amount comparison.

Nothing is backfilled and no existing row changes: every receipt recorded
before this migration has all three values, and a counter slip still requires
them (schemas/agreement_receipt.py).

Revision ID: ag09_receipt_amounts_nullable
Revises: ag08_agreement_schedule_start
Create Date: 2026-08-13
"""
import sqlalchemy as sa
from alembic import op

revision = "ag09_receipt_amounts_nullable"
down_revision = "ag08_agreement_schedule_start"
branch_labels = None
depends_on = None

_COLUMNS = ("amount", "tax_amount", "total_amount")


def upgrade() -> None:
    for col in _COLUMNS:
        op.alter_column("agreement_receipts", col,
                        existing_type=sa.Numeric(15, 2), nullable=True)
    # tax_amount carried server_default '0'. With the column nullable that
    # default is actively harmful: SQLAlchemy omits a None-valued column from
    # the INSERT when the column has a default, so an amount-less receipt was
    # stored with amount NULL, total NULL and tax 0.00 — a half-set that the
    # merged-row check then rejects on the next PATCH, and that reads as "this
    # receipt has a tax figure" to anyone looking at it.
    op.alter_column("agreement_receipts", "tax_amount", server_default=None)


def downgrade() -> None:
    # Rows recorded as amount-less would violate the restored NOT NULL. They
    # are exactly the delivery/service receipts this migration exists for, and
    # 0 is the only value that can stand in — the same lie the upgrade note
    # explains, accepted here because a downgrade has no better option.
    for col in _COLUMNS:
        op.execute(f"UPDATE agreement_receipts SET {col} = 0 WHERE {col} IS NULL")
        op.alter_column("agreement_receipts", col,
                        existing_type=sa.Numeric(15, 2), nullable=False)
    op.alter_column("agreement_receipts", "tax_amount", server_default=sa.text("0"))
