"""Equipment, so choosing a machine implies where it is.

HSE's example is exact: "if I picked the line 1 DSI (direct steam injection) as
a piece of equipment that caused an injury, the system should know that the DSI
is located only in the evaporator and no other department has one."

So this is a register keyed on equipment, carrying its location and department,
and the incident and inspection forms read the department off it rather than
asking twice.

Distinct from `ehs_safety_assets` on purpose. That register is extinguishers,
AEDs, eyewash stations and fall arrest gear — things that are inspected on a
cycle and whose inspection is the point. This one is production equipment that
appears on an incident form. One table serving both would need a "kind" column
that half the rows ignore, and the two get maintained by different people.

`asset_no` is the plant's own tag where there is one; unique so a spreadsheet
re-upload updates rather than duplicates.

Revision ID: 20260831_0013
Revises: 20260831_0012
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260831_0013"
down_revision = "20260831_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_equipment",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # The plant's own tag. Unique so re-uploading the spreadsheet updates.
        sa.Column("asset_no", sa.String(50), nullable=True, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("equipment_type", sa.String(100), nullable=True),
        sa.Column("manufacturer", sa.String(150), nullable=True),
        sa.Column("model", sa.String(150), nullable=True),
        sa.Column("serial_no", sa.String(100), nullable=True),

        # The whole point: equipment implies its place.
        sa.Column("location_id", UUID(as_uuid=True), nullable=True),
        sa.Column("location_path", sa.String(500), nullable=True),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("department_name", sa.String(255), nullable=True),

        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ehs_equipment_location", "ehs_equipment", ["location_id"])
    op.create_index("ix_ehs_equipment_active", "ehs_equipment", ["is_active"])


def downgrade() -> None:
    op.drop_table("ehs_equipment")
