"""How often the NC purchase sync should run itself.

The NC65 purchase mirror (orders + arrivals) has never had a schedule. The
only way it moved was somebody opening Portal -> Admin -> NC Purchase Sync and
pressing the button, which means the age of every PO and goods receipt in
UniOps was a function of who remembered. The same gap in the WMS mirror left
production reading an empty locations table for two days.

The interval is company config rather than an env var so an admin can change it
without a redeploy, and so it is visible in the same screen as the sync itself.

NULL means "not configured" and resolves to the 60-minute default in
app/tasks/nc_purchase_sync_scheduler.py — deliberately NOT a server_default, so
"nobody has chosen" stays distinguishable from "somebody chose 60", the
distinction `notification_channel` lost when a non-null default shadowed the
switch above it. 0 means the schedule is off and the button is the only trigger.

Revision ID: ai01_nc_sync_interval
Revises: ah01_po_line_planned_arrival
Create Date: 2026-08-18
"""
import sqlalchemy as sa
from alembic import op

revision = "ai01_nc_sync_interval"
down_revision = "ah01_po_line_planned_arrival"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_config",
        sa.Column("nc_purchase_sync_interval_minutes", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_config", "nc_purchase_sync_interval_minutes")
