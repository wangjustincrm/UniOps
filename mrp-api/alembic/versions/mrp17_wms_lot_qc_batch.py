"""wms_inventory_lots.qc_batch — LOTATT04, NC's QC batch number.

The mirror only carried the supplier batch (LOTATT05). LOTATT04 holds NC's
own batch number on raw materials and packaging, which is what the Inventory
module's WMS Inventory screen and its NC comparison need (design
2026-10-14-inventory-module §4.4). Filled by the next WMS sync; NULL until
then, and NULL on finished goods, where NC's batch sits in LOTATT05.

Revision id "mrp17_wms_lot_qc_batch" is 22 characters (limit 32).
"""
import sqlalchemy as sa
from alembic import op

revision = "mrp17_wms_lot_qc_batch"
down_revision = "mrp16_sync_state_last_success"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("wms_inventory_lots", sa.Column("qc_batch", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("wms_inventory_lots", "qc_batch")
