"""assistant_saved_questions: questions a person marked to re-ask with one click

Revision ID: as02_assistant_saved_q
Revises: pr01_fixed_asset_id
Create Date: 2026-10-02

People ask the assistant for the same report every week — "type 1 POs not yet
fully received, excluding milk" — and had to type it out each time. Up to ten
per person, kept server-side so the list follows them across the five front-ends
and across machines; browser storage would do neither.

Its own table for the same reason as assistant_usage: it is about the assistant,
not about a document, and can be dropped without touching business records.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "as02_assistant_saved_q"
down_revision = "pr01_fixed_asset_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_saved_questions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("question", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "question",
                            name="uq_assistant_saved_q_user_question"),
    )


def downgrade() -> None:
    op.drop_table("assistant_saved_questions")
