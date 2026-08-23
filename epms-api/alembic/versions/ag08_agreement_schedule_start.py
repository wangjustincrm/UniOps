"""Let a recurring agreement start its schedule later than its validity window.

Raised by the user after ag07: an agreement is routinely entered into the
system a year or two into its life. The schedule is generated from `valid_from`
— the CONTRACT's start — so those first periods are rows for invoices that were
paid outside this system and will never arrive here. They stay `pending`, the
overdue sweep flips them, and the owner is emailed about them every day.

`schedule_start_date` is the first date a period may be EXPECTED on. The period
grid itself is still derived from `valid_from`, so labels and quarterly/yearly
anchors remain the contract's; only the rows before this date are dropped, and
the survivors are renumbered from 1.

NULL means "from valid_from" — exactly today's behaviour, which is why nothing
is backfilled: an agreement created before this column existed has a schedule
that was already generated, and inventing a start date for it now would not
change those rows anyway (ensure_period_rows is idempotent by existence).

Revision ID: ag08_agreement_schedule_start
Revises: ag07_receipt_vendor_id
Create Date: 2026-08-13
"""
import sqlalchemy as sa
from alembic import op

revision = "ag08_agreement_schedule_start"
down_revision = "ag07_receipt_vendor_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchase_agreements",
        sa.Column("schedule_start_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("purchase_agreements", "schedule_start_date")
