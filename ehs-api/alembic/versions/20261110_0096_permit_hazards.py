"""Permits, round 12, PMT-01: hazards and controls picked from HSE's lists.

`hazard_items` and `control_items`, snapshots [{id, label}], as on a JSA step.
Nothing to backfill.

Revision ID: 20261110_0096
Revises: 20261110_0095
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261110_0096"
down_revision = "20261110_0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ("hazard_items", "control_items"):
        op.add_column("ehs_permits", sa.Column(col, JSONB, nullable=False, server_default="[]"))


def downgrade() -> None:
    for col in ("control_items", "hazard_items"):
        op.drop_column("ehs_permits", col)
