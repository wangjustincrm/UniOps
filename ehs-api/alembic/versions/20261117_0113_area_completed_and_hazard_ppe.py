"""UAT 15: the area that completed a cause's work, and PPE on a hazard.

TC-05: "make the 'department that completed the work' picker the same as
'Where did this happen' picker" — the plant area, its path kept as it read
when picked. A department recorded before stays on the cause.
TC-15: "beside the 'controls in place' add a PPE pick list" — [{id, label}]
from HSE's PPE list, as the controls are.

Revision ID: 20261117_0113
Revises: 20261116_0112
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261117_0113"
down_revision = "20261116_0112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_causes", sa.Column("completed_by_location_id", UUID(as_uuid=True), nullable=True))
    op.add_column("ehs_causes", sa.Column("completed_by_location_path", sa.String(500), nullable=True))
    op.add_column("ehs_hazards", sa.Column("hazard_ppe", JSONB, nullable=False,
                                           server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("ehs_hazards", "hazard_ppe")
    op.drop_column("ehs_causes", "completed_by_location_path")
    op.drop_column("ehs_causes", "completed_by_location_id")
