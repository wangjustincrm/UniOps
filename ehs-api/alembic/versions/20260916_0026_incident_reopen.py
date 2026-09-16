"""Record who reopened a closed incident, and when.

A closed incident is a finished record — what an inspector is shown, what the
corrective actions came from, what the statutory clocks were measured against.
Until now nothing stopped it being edited afterwards, which is how the first
acceptance round found it: "incidents are still updatable after the incidents
are reviewed or closed and they should be made read only."

Refusing the edit needs somewhere for a genuine correction to go, or people
raise a second incident for the same event, which is worse than an edit
anybody can see. So closing freezes it and reopening is a deliberate act with
a name against it. These three columns are that record.

Revision ID: 20260916_0026
Revises: 20260902_0025
Create Date: 2026-09-16
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260916_0026"
down_revision = "20260902_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_incidents",
                  sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True))
    # ON DELETE SET NULL with the name kept beside it, like every other person
    # on these records: the record outlives the account.
    op.add_column("ehs_incidents",
                  sa.Column("reopened_by", UUID(as_uuid=True), nullable=True))
    op.add_column("ehs_incidents",
                  sa.Column("reopened_by_name", sa.String(255), nullable=True))
    op.create_foreign_key(
        "fk_ehs_incidents_reopened_by", "ehs_incidents", "users",
        ["reopened_by"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_ehs_incidents_reopened_by", "ehs_incidents", type_="foreignkey")
    op.drop_column("ehs_incidents", "reopened_by_name")
    op.drop_column("ehs_incidents", "reopened_by")
    op.drop_column("ehs_incidents", "reopened_at")
