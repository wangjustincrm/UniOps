"""The Production Chronology on an investigation (UAT 13).

Revision ID: 20261115_0110
Revises: 20261115_0109
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261115_0110"
down_revision = "20261115_0109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_incident_investigations", sa.Column(
        "production_chronology", JSONB, nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("ehs_incident_investigations", "production_chronology")
