"""Emergency drills — the record, and the two numbers that make it one.

A fire drill is a measurement, not a meeting that happened: how long to get
everyone out, and was everyone accounted for at the muster point. Both are
columns rather than sentences in a notes field, because a drill whose time got
worse is the finding and a drill where somebody was missing is a much bigger
one, and neither is searchable in prose.

`all_accounted_for` is nullable deliberately. "Nobody checked" is a third state
and a worse one than "somebody was missing" — a boolean defaulting to true
would record the worst case as the best.

`what_failed` is separate from `observations` so it cannot be softened into the
paragraph above it. Every drill report ever written says it generally went
well; the useful half is the part that did not.

Emergency plans themselves are not a new table — they are controlled documents
with a review cycle and a read-and-sign list, which the document library
already is. This adds the category.

Revision ID: 20260902_0023
Revises: 20260901_0022
Create Date: 2026-09-02
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260902_0023"
down_revision = "20260901_0022"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "ehs_drills",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("drill_no", sa.String(20), nullable=False, unique=True),
        sa.Column("drill_type", sa.String(20), nullable=False, index=True),
        sa.Column("scenario", sa.Text, nullable=False),
        sa.Column("planned_for", sa.Date, nullable=True),
        sa.Column("held_on", sa.Date, nullable=True, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("location_id", UUID(as_uuid=True), nullable=True),
        sa.Column("location_path", sa.String(500), nullable=True),
        sa.Column("areas", JSONB, nullable=False, server_default="[]"),
        sa.Column("is_announced", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("participants_count", sa.Integer, nullable=True),
        # Seconds, not minutes: the difference between 3:10 and 3:50 is the
        # finding, and minutes rounds it away.
        sa.Column("evacuation_seconds", sa.Integer, nullable=True),
        # Nullable on purpose — "nobody checked" is a third state.
        sa.Column("all_accounted_for", sa.Boolean, nullable=True),
        sa.Column("missing_note", sa.String(300), nullable=True),
        sa.Column("observations", sa.Text, nullable=True),
        sa.Column("what_worked", sa.Text, nullable=True),
        sa.Column("what_failed", sa.Text, nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="planned",
                  index=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("evaluated_by_name", sa.String(255), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    op.create_table(
        "ehs_drill_participants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("drill_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_drills.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("person_name", sa.String(255), nullable=False),
        sa.Column("capacity", sa.String(20), nullable=False, server_default="participant"),
        sa.Column("note", sa.String(300), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("ehs_drill_participants")
    op.drop_table("ehs_drills")
