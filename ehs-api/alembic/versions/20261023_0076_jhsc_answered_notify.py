"""Who is told when a JHSC recommendation is answered.

Round 9: "create a 'who is told' for the JHSC recommendation when a root cause
corrective action form is completed. log this communication on the jhsc
recommendation to track it."

Revision ID: 20261023_0076
Revises: 20261022_0075
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261023_0076"
down_revision = "20261022_0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_config", sa.Column(
        "jhsc_answered_notify", JSONB(), nullable=False,
        server_default=sa.text("""'{"people": [], "sender": true}'::jsonb""")))


def downgrade() -> None:
    op.drop_column("ehs_config", "jhsc_answered_notify")
