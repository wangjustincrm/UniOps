"""The equipment register on incidents.

Round 10 (open box): "add this equipment as a picker on all forms so that we
can identify what piece of equipment contributed to the incidents … Allow
reports to be run against the equipment." Hazards, job analyses and permits
already carry equipment_id; an incident now does too. The free-text
"equipment involved" stays, for what the register does not list.

Revision ID: 20261028_0080
Revises: 20261027_0079
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261028_0080"
down_revision = "20261027_0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_incidents", sa.Column(
        "equipment_id", UUID(as_uuid=True),
        sa.ForeignKey("ehs_equipment.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_ehs_incidents_equipment_id", "ehs_incidents", ["equipment_id"])


def downgrade() -> None:
    op.drop_index("ix_ehs_incidents_equipment_id", table_name="ehs_incidents")
    op.drop_column("ehs_incidents", "equipment_id")
