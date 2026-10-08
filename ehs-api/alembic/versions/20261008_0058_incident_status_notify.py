"""Who is told when an incident's status changes.

Round 5, TC-15: "provide a way to manage the recipients list on incidents when
every status changes on an incident form. add this to the list of 'who is
told'."

One list per change — investigation started, reviewed by HSE, closed, reopened
— held beside the per-type lists for a new report. Empty for every one of them
to begin with: these changes have never sent anything, so an empty list keeps
today's behaviour exactly until HSE names somebody.

Revision ID: 20261008_0058
Revises: 20261007_0057
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261008_0058"
down_revision = "20261007_0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_config", sa.Column(
        "incident_status_notify", JSONB, nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("ehs_config", "incident_status_notify")
