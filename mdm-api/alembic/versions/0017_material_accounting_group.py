"""The ERP's material classification was already in the payload, unpromoted.

MRP's Inventory feature has to exclude raw milk from both stock and on-order
figures (business rule, 2026-08-17: NC material base class **0101 Raw Milk** is
never counted). The classification is NC's `BD_MARBASCLASS` — 28 materials sit
in 0101 there — and until now UniOps had no column carrying it, so the only way
to express the rule would have been a hardcoded list of codes. This codebase
has been burned by code-prefix and code-list rules more than once: CR0059
"Pasteurized Milk" is classified as raw milk while carrying a plain raw-material
prefix, so a prefix rule gets it wrong in both directions.

The value turned out to be present all along. The ERP webapi payload each
`erp_materials` row stores in `raw_payload` carries:

    "accounting_group": "0101", "accounting_group_name": "Raw Milk"

on **2,573 of 2,573** rows, and its distribution matches NC's own classes
(0101 Raw Milk 27, 0102 Raw Ingredient 393, 02 Packaging 197, 03 Standardized
Milk 45, 04 Storage Silo Powder 96, 05 Finished Products 131, 06 Chemical 10,
07 Mechanical 1,640, 08 Laboratory 23, 98 Fee 10, 99 Test 1). It was simply
never promoted out of the JSON.

So this migration adds the mirror columns and the master-data columns, and
**backfills both from `raw_payload`** — no ERP re-sync is required, and the
rule works the moment the code ships. `erp_sync._map_material` maps the fields
from here on, and `material_sync` promotes them like it already promotes `exp`
and `part_product_family`.

`materials.erp_class_code` is indexed because MRP filters on it on every
inventory read.

One material differs between the two sources: NC's 0101 holds 28 codes and the
ERP mirror 27, the extra being `CR0014 Cow Cream`. It exists nowhere in UniOps
— not in `erp_materials`, `materials`, `wms_inventory_lots`, nor on any PO line
— so nothing is missed by classifying from the mirror.

Revision ID: 0017_material_acct_group (shortened: alembic_version is varchar(32))
Revises: 0016_nc_sync_state
Create Date: 2026-08-17
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_material_acct_group"
down_revision = "0016_nc_sync_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("erp_materials", sa.Column("accounting_group", sa.String(20), nullable=True))
    op.add_column("erp_materials", sa.Column("accounting_group_name", sa.String(100), nullable=True))
    op.add_column("materials", sa.Column("erp_class_code", sa.String(20), nullable=True))
    op.add_column("materials", sa.Column("erp_class_name", sa.String(100), nullable=True))
    op.create_index("ix_materials_erp_class_code", "materials", ["erp_class_code"])

    # Backfill from the JSON the sync has been storing all along, so the rule
    # is live without waiting for an ERP sync run. `->>` yields NULL for a
    # missing key, which is what "the ERP did not classify this" should mean.
    op.execute("""
        update erp_materials
           set accounting_group      = raw_payload->>'accounting_group',
               accounting_group_name = raw_payload->>'accounting_group_name'
    """)
    # materials is keyed on `code`, which the sync sets from erp_part_no.
    op.execute("""
        update materials m
           set erp_class_code = e.accounting_group,
               erp_class_name = e.accounting_group_name
          from erp_materials e
         where e.erp_part_no = m.code
    """)


def downgrade() -> None:
    op.drop_index("ix_materials_erp_class_code", table_name="materials")
    op.drop_column("materials", "erp_class_name")
    op.drop_column("materials", "erp_class_code")
    op.drop_column("erp_materials", "accounting_group_name")
    op.drop_column("erp_materials", "accounting_group")
