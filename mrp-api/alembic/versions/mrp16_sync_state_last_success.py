"""When the mirror last actually got fresh data — as distinct from when we last tried.

`mrp_sync_state.last_synced_at` is written on every run, including the failed
ones and the empty-extract refusals (see wms_sync/service.py's
`_write_sync_state`). That is the right meaning for "when did we last attempt
this", and the wrong one for the only question the Inventory screen asks: *how
old is what I am looking at?* With one column doing both jobs, a WMS outage
would make the screen look freshly synced every few minutes while the numbers
on it quietly aged.

So: `last_synced_at` keeps meaning "last attempt", and `last_success_at` is
added to mean "the snapshot on screen was taken then". Existing rows are
backfilled from `last_synced_at` where the last attempt succeeded — a new
column that starts empty everywhere is a bug this project has already shipped
once (a mirror column added without a follow-up sync), and here the correct
value is already sitting in the row.

Revision ID: mrp16_sync_state_last_success
Revises: mrp15_wms_lot_uom
Create Date: 2026-08-18
"""
import sqlalchemy as sa
from alembic import op

revision = "mrp16_sync_state_last_success"
down_revision = "mrp15_wms_lot_uom"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mrp_sync_state",
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
    )
    # A row whose last attempt succeeded is a row whose snapshot is that old.
    # 'empty_extract' is deliberately NOT backfilled: it means the extract came
    # back empty and the PREVIOUS snapshot was kept, so its attempt time says
    # nothing about the age of the data being shown.
    op.execute(
        "UPDATE mrp_sync_state SET last_success_at = last_synced_at "
        "WHERE status = 'success'"
    )


def downgrade() -> None:
    op.drop_column("mrp_sync_state", "last_success_at")
