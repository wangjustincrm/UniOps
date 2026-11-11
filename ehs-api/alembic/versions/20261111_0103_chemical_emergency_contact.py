"""Chemical emergency health contact and hazard statements (round 12, CHM-03/04).

Revision ID: 20261111_0103
Revises: 20261111_0102

Labels already approved keep what they were approved with: a label is the
record of what went on the drum. They carry the new contact only once a new
version is drafted and approved.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261111_0103"
down_revision = "20261111_0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_chemicals", sa.Column("emergency_contact", sa.String(300), nullable=True))
    op.add_column("ehs_chemicals", sa.Column(
        "hazard_statements", JSONB, nullable=False, server_default="[]"))
    op.add_column("ehs_chemical_labels",
                  sa.Column("emergency_contact", sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_chemical_labels", "emergency_contact")
    op.drop_column("ehs_chemicals", "hazard_statements")
    op.drop_column("ehs_chemicals", "emergency_contact")
