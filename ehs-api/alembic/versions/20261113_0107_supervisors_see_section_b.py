"""A rule: supervisors may see an incident's Section B (UAT 13, question E).

Off, as Kirk asked: "leave it off the supervisor view for now but make it an
option under the rules section so that it can be turned on at a later date."

Revision ID: 20261113_0107
Revises: 20261113_0106
"""
import sqlalchemy as sa
from alembic import op

revision = "20261113_0107"
down_revision = "20261113_0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_config", sa.Column(
        "supervisors_see_section_b", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("ehs_config", "supervisors_see_section_b")
