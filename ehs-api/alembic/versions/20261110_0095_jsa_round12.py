"""Job safety analyses, round 12.

* JSA-01 / D8: a JSA records the equipment System it is for (`system_id`).
  Shown by the System's current name, like a machine's (B2).
* JSA-03: PPE picked from HSE's PPE list (`ppe_items`, snapshots [{id, label}]).
  The words already typed in `required_ppe` stay, shown read-only beside it.
* JSA-05 / Q12 ("agree one per step"): each step picks its hazards and its
  controls from HSE's lists (`hazard_items`, `control_items`). The step's own
  words stay; approval counts either.

Nothing is backfilled: the typed PPE and step text are not list entries.

Revision ID: 20261110_0095
Revises: 20261110_0094
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261110_0095"
down_revision = "20261110_0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_jsa", sa.Column("system_id", UUID(as_uuid=True), nullable=True))
    op.add_column("ehs_jsa", sa.Column("ppe_items", JSONB, nullable=False, server_default="[]"))
    for col in ("hazard_items", "control_items"):
        op.add_column("ehs_jsa_steps", sa.Column(col, JSONB, nullable=False, server_default="[]"))


def downgrade() -> None:
    for col in ("control_items", "hazard_items"):
        op.drop_column("ehs_jsa_steps", col)
    op.drop_column("ehs_jsa", "ppe_items")
    op.drop_column("ehs_jsa", "system_id")
