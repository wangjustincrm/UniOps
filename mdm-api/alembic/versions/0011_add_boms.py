"""Canonical BOM tables: boms / bom_lines / bom_substitutes (MRP phase0 task 5)

Revision ID: 0011_add_boms
Revises: 0010_add_nc_bom_mirror
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0011_add_boms"
down_revision = "0010_add_nc_bom_mirror"
branch_labels = None
depends_on = None


def _ts_cols():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade():
    op.create_table(
        "boms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("product_material_code", sa.String(50), nullable=False),
        sa.Column("bom_type", sa.String(20)),
        sa.Column("version", sa.String(30)),
        sa.Column("factory_code", sa.String(50)),
        sa.Column("status", sa.String(20), nullable=False, server_default="approved"),
        sa.Column("effective_from", sa.Date()),
        sa.Column("effective_to", sa.Date()),
        sa.Column("yield_rate", sa.Numeric(18, 6), nullable=False, server_default="1"),
        sa.Column("nc_source_pk", sa.String(50), nullable=False),
        *_ts_cols(),
        sa.UniqueConstraint("nc_source_pk", name="uq_boms_nc_pk"),
    )
    op.create_index("ix_boms_product_material_code", "boms", ["product_material_code"])

    op.create_table(
        "bom_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("bom_id", UUID(as_uuid=True), sa.ForeignKey("boms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("component_material_code", sa.String(50), nullable=False),
        sa.Column("qty_per", sa.Numeric(18, 6), nullable=False),
        sa.Column("uom", sa.String(20)),
        sa.Column("scrap_rate", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("effective_from", sa.Date()),
        sa.Column("effective_to", sa.Date()),
        sa.Column("nc_source_pk", sa.String(50)),
        *_ts_cols(),
        sa.UniqueConstraint("nc_source_pk", name="uq_bom_lines_nc_pk"),
    )
    op.create_index("ix_bom_lines_bom_id", "bom_lines", ["bom_id"])
    op.create_index("ix_bom_lines_component_material_code", "bom_lines", ["component_material_code"])

    op.create_table(
        "bom_substitutes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("bom_line_id", UUID(as_uuid=True), sa.ForeignKey("bom_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("substitute_material_code", sa.String(50), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("mode", sa.String(10), nullable=False, server_default="suggest"),
        sa.Column("nc_source_pk", sa.String(50)),
        *_ts_cols(),
        sa.UniqueConstraint("nc_source_pk", name="uq_bom_substitutes_nc_pk"),
    )
    op.create_index("ix_bom_substitutes_bom_line_id", "bom_substitutes", ["bom_line_id"])


def downgrade():
    op.drop_table("bom_substitutes")
    op.drop_table("bom_lines")
    op.drop_table("boms")
