"""Where it happened, on a JHSC recommendation.

Round 8 (open box): "when making a recommendation, include the 'where it
happened' field picker and include this info on the email."

Revision ID: 20261020_0072
Revises: 20261020_0071
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261020_0072"
down_revision = "20261020_0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_jhsc_recommendations", sa.Column("location_id", UUID(as_uuid=True), nullable=True))
    op.add_column("ehs_jhsc_recommendations", sa.Column("location_path", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_jhsc_recommendations", "location_path")
    op.drop_column("ehs_jhsc_recommendations", "location_id")
