"""company_config.module_taglines — per-module taglines

Revision ID: x7_add_module_taglines
Revises: x6_match_tolerance
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "x7_add_module_taglines"
down_revision = "x6_match_tolerance"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("company_config",
                  sa.Column("module_taglines", JSONB(),
                            nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("company_config", "module_taglines")
