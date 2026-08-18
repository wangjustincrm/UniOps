"""The unit a WMS quantity is measured in, taken from WMS.

Reported by the user, 2026-08-18: the Inventory screen showed S0093 lot
HGC1993989 as **3 PIECES**; the warehouse says **2.8000 KG**. Both halves were
wrong, and the unit was wrong at the source rather than in the rendering.

The screen was labelling WMS quantities with the ERP's unit
(`materials.base_uom`, synced from the ERP webapi's `unit_MEAS`). For raw
materials the two agree. For finished goods they do not: the ERP counts S0093
in PIECES because that is how it is sold, the warehouse weighs it in KG because
that is how it is stored. Putting one system's unit on the other system's
number is simply mislabelling it.

The warehouse's own answer lives in its packaging ladder, not on the SKU:
`BAS_SKU.UOM`-style columns do exist but are `'EA'` on all 2,482 SKUs and are
plainly unmaintained. `BAS_PACKAGE_DETAILS` holds a level per pack unit, and the
BASE level (`PACKUOM = 'EA'`) carries the real unit in `UOMDESCR` — `STANDARD`
resolves to KG, `PMSTANDARD` and `TINBOTTOM` to PIECES, `LACTALIS25KG` to KG,
and so on across 11 pack ids. That reproduces the warehouse's own screen
exactly, including the reported row.

Verified read-only against the live instance before writing the sync: the join
is strictly 1:1 (3,429 extract rows in, 3,429 out), `(CUSTOMERID, PACKID)` is
unique at the base level across all 6,535 groups, and **no** SKU we hold stock
for lacks a base-level row, so nothing ends up unitless.

Nullable because the mirror must keep working if the warehouse ever adds a SKU
without one — a missing unit has to read as "not stated", never as a guess.

Revision ID: mrp15_wms_lot_uom
Revises: mrp14_wms_lot_locations
Create Date: 2026-08-18
"""
import sqlalchemy as sa
from alembic import op

revision = "mrp15_wms_lot_uom"
down_revision = "mrp14_wms_lot_locations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("wms_inventory_lots", sa.Column("uom", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("wms_inventory_lots", "uom")
