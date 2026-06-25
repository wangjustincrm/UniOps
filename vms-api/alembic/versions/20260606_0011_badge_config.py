"""Add structured badge_config to vms_config.

Replaces the freeform badge_templates HTML/CSS (whose editor never reached the
printed badge) with a single structured config object the renderer consumes.
Nullable-safe via server_default '{}'; the renderer falls back to code defaults
when empty, so an unapplied migration cannot 500 the badge — it just disables
editing.

Revision ID: 20260606_0011
Revises: 20260602_0010
Create Date: 2026-06-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260606_0011"
down_revision = "20260602_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vms_config",
        sa.Column(
            "badge_config",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("vms_config", "badge_config")
