"""How a due date on My Actions is coloured (round 12, ACT-05).

Revision ID: 20261111_0099
Revises: 20261111_0098
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261111_0099"
down_revision = "20261111_0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_config", sa.Column(
        "due_bands", JSONB, nullable=False,
        server_default=sa.text("""'{"green": 10, "yellow": 5, "red": 2}'::jsonb""")))


def downgrade() -> None:
    op.drop_column("ehs_config", "due_bands")
