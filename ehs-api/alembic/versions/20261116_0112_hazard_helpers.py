"""Staff who helped with a hazard assessment (UAT 14).

Kirk: "under the owner and review date field, add a staff picker to allow
additional staff to be selected as they helped with the hazard assessment."
Kept as [{id, name}], the name as it read when picked.

Revision ID: 20261116_0112
Revises: 20261116_0111
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261116_0112"
down_revision = "20261116_0111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_hazards", sa.Column("helpers", JSONB, nullable=False,
                                           server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("ehs_hazards", "helpers")
