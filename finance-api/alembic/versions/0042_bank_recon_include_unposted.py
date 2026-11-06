"""Bank reconciliation: per-period "include unposted vouchers" basis.

User, 2026-10-14: the bank reconciliation should tick "include unposted
vouchers" by default, like the Budget and Account Balance pages. The basis is
stored on the period because every ledger read of a period — auto-match,
manual matches, the difference and the signed snapshot — has to use the same
one.

Existing periods: a finalized period keeps the posted-only basis it was signed
on (its snapshot is frozen anyway, and a Reopen must not silently change what
was signed). Open periods move to the new default.

Revision ID: 0042_bank_recon_include_unposted
Revises: 0041_nc_cost_vouchers
"""
import sqlalchemy as sa
from alembic import op

revision = "0042_bank_recon_include_unposted"
down_revision = "0041_nc_cost_vouchers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bank_reconciliations", sa.Column(
        "include_unposted", sa.Boolean(), nullable=False,
        server_default=sa.text("true")))
    op.execute("update bank_reconciliations set include_unposted = false "
               "where status = 'finalized'")


def downgrade() -> None:
    op.drop_column("bank_reconciliations", "include_unposted")
