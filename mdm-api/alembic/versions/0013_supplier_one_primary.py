"""material_suppliers — at most one PRIMARY supplier per material.

Partial unique index on material_code WHERE is_primary — a plain
(unfiltered) unique index would forbid multiple non-primary candidate
suppliers for the same material, which is the normal case (Phase 1
purchase-suggestion logic picks the default supplier via is_primary, but a
material can legitimately have several suppliers on file). 0012 already
shipped without this constraint — this is a separate migration, not an edit
to 0012.

Revision ID: 0013_supplier_one_primary
Revises: 0012_add_material_suppliers
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_supplier_one_primary"
down_revision = "0012_add_material_suppliers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "uq_material_suppliers_one_primary",
        "material_suppliers",
        ["material_code"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )


def downgrade():
    op.drop_index("uq_material_suppliers_one_primary", table_name="material_suppliers")
