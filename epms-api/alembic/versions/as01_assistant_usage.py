"""assistant_usage: one row per model call, so spend is a fact rather than a log line

Revision ID: as01_assistant_usage
Revises: nc06_po_refetch_requests
Create Date: 2026-09-13

The assistant logged its token counts with log.info and nothing came out. The
application logger has no handler on the request path in this deployment — the
same reason approval_client's outbound lines are absent — so what looked like
accounting was writing to nowhere. Spend on a shared API key is not something to
learn about from a bill, and a quota has to count something that exists, so the
counts go in a table.

Deliberately its own table rather than a column on anything: it is operational
data about the assistant, not about a document, and it should be droppable
without touching business records.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "as01_assistant_usage"
down_revision = "nc06_po_refetch_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_usage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        # Who spent it. Kept as a plain column, not a foreign key: usage history
        # should survive a user being removed, and it is never joined back.
        sa.Column("user_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("stage", sa.String(32), nullable=False),   # plan | narrate | …
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        # Cache reads bill at a tenth of the input rate, so they have to be
        # separable or every estimate is high.
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    # The query a daily quota asks: how much has this person used today.
    op.create_index("ix_assistant_usage_user_day", "assistant_usage",
                    ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_assistant_usage_user_day", table_name="assistant_usage")
    op.drop_table("assistant_usage")
