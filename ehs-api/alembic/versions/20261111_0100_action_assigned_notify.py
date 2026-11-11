"""Who is told when a corrective action is given to somebody, and who raised it.

Round 12, WHO-01 / TC-13 / D13. Existing actions keep raised_by empty: nobody
recorded who raised them, and guessing would name the wrong person.

Revision ID: 20261111_0100
Revises: 20261111_0099
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261111_0100"
down_revision = "20261111_0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_actions", sa.Column(
        "raised_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True))
    op.add_column("ehs_actions", sa.Column("raised_by_name", sa.String(255), nullable=True))
    op.add_column("ehs_config", sa.Column(
        "action_assigned_notify", JSONB, nullable=False,
        server_default=sa.text("""'{"people": [], "previous_owner": true, "reporter": false, "supervisor": false}'::jsonb""")))


def downgrade() -> None:
    op.drop_column("ehs_config", "action_assigned_notify")
    op.drop_column("ehs_actions", "raised_by_name")
    op.drop_column("ehs_actions", "raised_by")
