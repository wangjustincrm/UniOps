"""bom_lines: secondary (assistant) unit qty/uom (MRP phase0 patch 3)

S-prefixed finished-good BOM lines carry a SECOND unit alongside the main
qty_per/uom (<- NC BD_BOM_B.NASSITEMNUM/CASSMEASUREID, e.g. main unit KG +
secondary unit PIECES; conversion is maintained in the material master).
Both nullable — most bom_lines are single-unit and never populate these.

Revision ID: 0014_bom_line_secondary_uom
Revises: 0013_material_suppliers_one_primary
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_bom_line_secondary_uom"
down_revision = "0013_material_suppliers_one_primary"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bom_lines", sa.Column("qty_per_secondary", sa.Numeric(18, 6), nullable=True))
    op.add_column("bom_lines", sa.Column("uom_secondary", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("bom_lines", "uom_secondary")
    op.drop_column("bom_lines", "qty_per_secondary")
