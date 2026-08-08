"""erp_materials.exp / part_product_family — NC ERP material interface fields

Task 3 scope extension: the NC ERP material HTTP interface returns `exp`
(shelf-life in months) and `part_PRODUCT_FAMILY` (product family) on every
material row. Task 2's erp_material mirror didn't have dedicated columns for
them, but the full response row is already stored in raw_payload (JSONB) for
every previously-synced row, so this migration backfills the new columns
from raw_payload instead of requiring a re-sync. The ERP key casing observed
is `exp` / `part_PRODUCT_FAMILY`; a lower-case fallback is included in case a
given payload was captured differently.

Revision ID: 0008_erp_material_exp_family
Revises: 0007_create_materials
Create Date: 2026-08-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_erp_material_exp_family"
down_revision = "0007_create_materials"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("erp_materials", sa.Column("exp", sa.Integer(), nullable=True))
    op.add_column("erp_materials", sa.Column("part_product_family", sa.String(100), nullable=True))

    # Backfill from raw_payload for rows synced before these columns existed.
    op.execute("""
        UPDATE erp_materials
        SET exp = CASE
            WHEN COALESCE(raw_payload->>'exp', raw_payload->>'EXP') ~ '^-?[0-9]+$'
                THEN (COALESCE(raw_payload->>'exp', raw_payload->>'EXP'))::integer
            ELSE NULL
        END
        WHERE raw_payload ? 'exp' OR raw_payload ? 'EXP'
    """)
    op.execute("""
        UPDATE erp_materials
        SET part_product_family = COALESCE(
            raw_payload->>'part_PRODUCT_FAMILY',
            raw_payload->>'part_product_family'
        )
        WHERE raw_payload ? 'part_PRODUCT_FAMILY' OR raw_payload ? 'part_product_family'
    """)


def downgrade():
    op.drop_column("erp_materials", "part_product_family")
    op.drop_column("erp_materials", "exp")
