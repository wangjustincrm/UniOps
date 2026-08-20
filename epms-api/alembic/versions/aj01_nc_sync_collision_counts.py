"""Report what the NC purchase sync did about colliding document numbers.

NC's ``vbillcode`` is not unique — 1,682 approved orders under 1,645 numbers —
so the mirror has always had to do SOMETHING when two live orders arrive with
one number. What it did was drop the loser and everything under it, and say so
only by printing to the container log. Nobody reads a container log, so 15
orders and 16 goods receipts went missing inside the cutover without a single
run ever showing anything but "success". PO-019-2505-01 read as never received
for months on the strength of that silence.

The dropping is gone (the order is mirrored under a suffixed number now), but
the counting must not be: a document whose number is not the ERP's is a fact
finance needs when they go looking for it, and a genuine skip — no free number
within the bound — must be impossible to miss.

Both columns are NOT NULL DEFAULT 0: historical runs did not measure this, and
0 is the honest reading for them, since the behaviour they had was to skip
silently and the count of what they skipped is unrecoverable.

Revision ID: aj01_nc_sync_collision_counts
Revises: ai01_nc_sync_interval
Create Date: 2026-08-19
"""
import sqlalchemy as sa
from alembic import op

revision = "aj01_nc_sync_collision_counts"
down_revision = "ai01_nc_sync_interval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nc_purchase_sync_runs",
        sa.Column("renamed_number_collision", sa.Integer(), nullable=False,
                  server_default="0"),
    )
    op.add_column(
        "nc_purchase_sync_runs",
        sa.Column("skipped_number_collision", sa.Integer(), nullable=False,
                  server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("nc_purchase_sync_runs", "skipped_number_collision")
    op.drop_column("nc_purchase_sync_runs", "renamed_number_collision")
