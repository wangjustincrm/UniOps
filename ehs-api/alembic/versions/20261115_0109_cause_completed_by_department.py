"""Which department completed the work on a cause, and when (UAT 13).

Revision ID: 20261115_0109
Revises: 20261115_0108
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261115_0109"
down_revision = "20261115_0108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_causes", sa.Column("completed_by_department_id", UUID(as_uuid=True), nullable=True))
    op.add_column("ehs_causes", sa.Column("completed_by_department_name", sa.String(255), nullable=True))
    op.add_column("ehs_causes", sa.Column("completed_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_causes", "completed_on")
    op.drop_column("ehs_causes", "completed_by_department_name")
    op.drop_column("ehs_causes", "completed_by_department_id")
