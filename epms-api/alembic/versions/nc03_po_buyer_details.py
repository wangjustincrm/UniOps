"""add buyer-supplied detail columns for NC-imported POs

Revision ID: nc03_po_buyer_details
Revises: nc02_nc_cutover
Create Date: 2026-08-07

purchase_orders.notes belongs to the NC mirror — every incremental sync
rewrites it with NC's own memo plus [NC Paid] / [NC Closed <date>] markers
that finance reads. buyer_notes/incoterms therefore get their own columns,
which the sync writer deliberately never touches. buyer_edited_at marks a PO
whose tax rate was set by hand so the sync can keep that rate instead of
overwriting it with NC's.
"""
import sqlalchemy as sa
from alembic import op

revision = "nc03_po_buyer_details"
down_revision = "nc02_nc_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_orders", sa.Column("buyer_notes", sa.Text(), nullable=True))
    op.add_column("purchase_orders", sa.Column("incoterms", sa.String(length=100), nullable=True))
    op.add_column(
        "purchase_orders",
        sa.Column("buyer_edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("po_line_items", sa.Column("sample", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("po_line_items", "sample")
    op.drop_column("purchase_orders", "buyer_edited_at")
    op.drop_column("purchase_orders", "incoterms")
    op.drop_column("purchase_orders", "buyer_notes")
