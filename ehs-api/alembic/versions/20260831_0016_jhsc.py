"""The Joint Health and Safety Committee.

OHSA s.9. Twenty-three members here, twenty of them certified, meeting monthly
with an inspection of one department after each meeting.

Three things this stores that a minutes document does not:

**Representation per member.** At least half the committee must be workers who
do not exercise managerial functions. A list of names cannot be checked against
that requirement; a column can.

**Attendance per member per meeting.** "Who has missed three in a row" is a
question the co-chairs actually ask, and a headcount cannot answer it.

**Recommendations as rows.** s.9(20) gives the employer twenty-one days to
respond in writing. The countdown lives in ehs_statutory_deadlines with the
MOL and WSIB clocks so one sweep chases all of them; the response text and its
outcome live here.

Revision ID: 20260831_0016
Revises: 20260831_0015
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260831_0016"
down_revision = "20260831_0015"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "ehs_jhsc_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("department_name", sa.String(255), nullable=True),
        sa.Column("representation", sa.String(12), nullable=False, server_default="worker"),
        sa.Column("committee_role", sa.String(12), nullable=False, server_default="member"),
        sa.Column("is_certified", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("certified_on", sa.Date, nullable=True),
        sa.Column("term_start", sa.Date, nullable=True),
        sa.Column("term_end", sa.Date, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    op.create_table(
        "ehs_jhsc_meetings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("meeting_no", sa.String(20), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("held_on", sa.Date, nullable=False, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("location_text", sa.String(200), nullable=True),
        sa.Column("agenda", sa.Text, nullable=True),
        sa.Column("minutes", sa.Text, nullable=True),
        sa.Column("inspection_department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("inspection_department_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="scheduled",
                  index=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("minutes_file_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )

    op.create_table(
        "ehs_jhsc_attendance",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("meeting_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_jhsc_meetings.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("member_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_jhsc_members.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("representation", sa.String(12), nullable=True),
        sa.Column("attendance", sa.String(10), nullable=False, server_default="present"),
        sa.UniqueConstraint("meeting_id", "member_id", name="uq_ehs_jhsc_attendance"),
    )

    op.create_table(
        "ehs_jhsc_recommendations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("meeting_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_jhsc_meetings.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("seq", sa.Integer, nullable=False, server_default="1"),
        sa.Column("recommendation", sa.Text, nullable=False),
        sa.Column("raised_by_name", sa.String(255), nullable=True),
        sa.Column("response", sa.Text, nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("responded_by_name", sa.String(255), nullable=True),
        sa.Column("outcome", sa.String(12), nullable=True),
        sa.Column("action_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_actions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )


def downgrade() -> None:
    op.drop_table("ehs_jhsc_recommendations")
    op.drop_table("ehs_jhsc_attendance")
    op.drop_table("ehs_jhsc_meetings")
    op.drop_table("ehs_jhsc_members")
