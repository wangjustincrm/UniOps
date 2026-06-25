"""units_of_measure master + seed of current built-in units

Unit symbols keep their case (kg, m², L, pcs) so existing stored line-item
units keep matching. Conversion rates (primary/secondary UOM per material)
are deferred to the future material/production module.

Revision ID: 0005_units_of_measure
Revises: 0004_business_partners
Create Date: 2026-06-18
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005_units_of_measure"
down_revision = "0004_business_partners"
branch_labels = None
depends_on = None


def upgrade():
    uom = op.create_table(
        "units_of_measure",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("dimension", sa.String(20), nullable=False, server_default="other"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_units_of_measure_code"),
    )
    op.create_index("ix_units_of_measure_code", "units_of_measure", ["code"], unique=True)

    def row(code, name, dim):
        return dict(id=uuid.uuid4(), code=code, name=name, dimension=dim, is_active=True)

    op.bulk_insert(uom, [
        row("pcs", "Pieces", "count"),
        row("kg", "Kilogram", "mass"),
        row("set", "Set", "count"),
        row("pair", "Pair", "count"),
        row("box", "Box", "count"),
        row("carton", "Carton", "count"),
        row("roll", "Roll", "count"),
        row("m", "Meter", "length"),
        row("m²", "Square Meter", "area"),
        row("L", "Liter", "volume"),
        row("hour", "Hour", "time"),
        row("month", "Month", "time"),
        row("lot", "Lot", "count"),
    ])


def downgrade():
    op.drop_index("ix_units_of_measure_code", table_name="units_of_measure")
    op.drop_table("units_of_measure")
