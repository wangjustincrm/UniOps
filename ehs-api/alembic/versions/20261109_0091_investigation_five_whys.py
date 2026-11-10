"""The 5 Whys on an investigation.

Round 12, INC-15/INC-16 and Q8: "the 5 why technique meets that requirement.
not every incident requires such a formal process, but any items categorized
as food safety would require it." Up to five answers; a food-safety
investigation is signed off once all five are given.

Revision ID: 20261109_0091
Revises: 20261109_0090
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261109_0091"
down_revision = "20261109_0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_incident_investigations", sa.Column(
        "five_whys", JSONB, nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("ehs_incident_investigations", "five_whys")
