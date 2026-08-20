"""Where each WMS lot physically sits.

The Inventory screen lists stock by supplier batch, and a planner looking at a
batch needs to know which locations it occupies — the internal lot number is
Flux's own identifier and means nothing on the warehouse floor, but the
location does.

`INV_LOT_LOC_ID` was surveyed in Phase 0 and recorded in the design doc's
appendix A as "库位/FEFO 明细视图需要时用". This is that moment. Surveyed again
read-only against the live instance before writing a line of sync code, which
settled three things a guess would have got wrong:

- A lot spans locations (25 lots sit in two or more; one is spread over 28), so
  this cannot be a column on `wms_inventory_lots`.
- The natural key needs `TRACEID` as well as `LOCATIONID`: `STAGECANADA` holds
  15 pallets of one lot, 700 each, distinguished by nothing else.
- The table's own `QCSTATUS` is NULL on all 3,549 rows and is not maintained,
  so quality status stays on the lot. Reading it from here would have silently
  blanked the column the same week it was added.

Snapshot semantics, matching `wms_inventory_lots`: the sync replaces the whole
table each run inside one transaction, so location and lot data can never
describe different moments.

Revision ID: mrp14_wms_lot_locations
Revises: mrp13_purchase_suggestions
Create Date: 2026-08-18
"""
import sqlalchemy as sa
from alembic import op

revision = "mrp14_wms_lot_locations"
down_revision = "mrp13_purchase_suggestions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wms_lot_locations",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("warehouse_id", sa.String(20), nullable=False),
        sa.Column("material_code", sa.String(50), nullable=False),
        sa.Column("lot_no", sa.String(50), nullable=False),
        sa.Column("location_id", sa.String(50), nullable=False),
        sa.Column("trace_id", sa.String(50), nullable=False),
        sa.Column("zone_id", sa.String(50), nullable=True),
        sa.Column("qty", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("qty_allocated", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("qty_onhold", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("sync_batch_id", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("warehouse_id", "material_code", "lot_no", "location_id",
                            "trace_id", name="uq_wms_lot_locations_natural_key"),
    )
    op.create_index("ix_wms_lot_locations_warehouse_id", "wms_lot_locations", ["warehouse_id"])
    op.create_index("ix_wms_lot_locations_material_code", "wms_lot_locations", ["material_code"])
    op.create_index("ix_wms_lot_locations_lot_no", "wms_lot_locations", ["lot_no"])
    op.create_index("ix_wms_lot_locations_location_id", "wms_lot_locations", ["location_id"])
    op.create_index("ix_wms_lot_locations_sync_batch_id", "wms_lot_locations", ["sync_batch_id"])
    # The lookup the table exists for: "where is this lot", once per expanded
    # batch row.
    op.create_index("ix_wms_lot_locations_lot", "wms_lot_locations",
                    ["material_code", "lot_no"])


def downgrade() -> None:
    op.drop_table("wms_lot_locations")
