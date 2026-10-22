"""The provider of training taken elsewhere.

Round 9, TC-07: "when recording training with an outside provider, provide a
space to enter the providers name and include that on the printed and exported
records."

Revision ID: 20261022_0074
Revises: 20261022_0073
"""
import sqlalchemy as sa
from alembic import op

revision = "20261022_0074"
down_revision = "20261022_0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_training_records", sa.Column("provider", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_training_records", "provider")
