"""Each person chooses which panels their dashboard shows, and in what order.

HSE asked for the dashboard to be configurable, and chose the "pick from the
chart library and arrange it" option when the PRD asked how. This is that,
kept to what it says: which of the panels that exist, in which order. Not a
layout engine — a plant safety dashboard with six panels does not need one,
and the panels themselves are already responsive.

Per user rather than per browser, because "every manager can customise their
own" means it follows them to the machine in the meeting room.

An empty or missing row means the default order, so nothing has to be seeded
and a new panel added later appears for everyone rather than being invisible
until each person opts in.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260923_0037" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260923_0037"
down_revision = "20260923_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_dashboard_layouts",
        sa.Column("user_id", sa.UUID(), primary_key=True),
        # The panel keys this person wants, in the order they want them.
        sa.Column("panels", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("ehs_dashboard_layouts")
