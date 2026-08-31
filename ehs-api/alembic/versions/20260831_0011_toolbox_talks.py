"""Toolbox talks: a monthly topic, discussed in a team meeting, signed by everyone there.

HSE described the whole cycle, and it is not the small thing v0.3 had it as:

    scheduled automatically on the first of each month → lands in each
    supervisor's to-do list → after the team meeting the supervisor and every
    attendee sign electronically → biweekly email reminders until signed off →
    completion rate on the management dashboard

The part that is genuinely new is the signature model. Everything else in this
module has one signer; a toolbox talk produces seven or eight at once, taken on
one device passed around after the meeting. HSE's own signed sample shows
exactly that — seven signatures inside two minutes, from one location.

So `ehs_tbt_signatures` is a child table rather than a column, and the roll-up
lives on the talk: how many signed, out of how many were expected. A talk with
six of eight signatures is the interesting case and needs to be visible as such.

`topic_document_id` points at file-api: the talk itself is a one-page PDF HSE
writes each month.

Revision ID: 20260831_0011
Revises: 20260831_0010
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260831_0011"
down_revision = "20260831_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_toolbox_talks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("talk_no", sa.String(20), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("topic", sa.Text, nullable=True),
        # The one-page PDF, in file-api.
        sa.Column("topic_document_id", UUID(as_uuid=True), nullable=True),
        # The month this belongs to, held as its first day so a month is a value
        # rather than a range every query has to reconstruct.
        sa.Column("period_month", sa.Date, nullable=False),
        sa.Column("is_published", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )

    # One delivery of the talk, by one supervisor to one team.
    op.create_table(
        "ehs_tbt_deliveries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("talk_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_toolbox_talks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supervisor_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("supervisor_name", sa.String(255), nullable=True),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("department_name", sa.String(255), nullable=True),
        sa.Column("location_id", UUID(as_uuid=True), nullable=True),
        # pending | delivered
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("held_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_attendees", sa.Integer, nullable=False, server_default="0"),
        sa.Column("signed_count", sa.Integer, nullable=False, server_default="0"),
        # When the last reminder went out, so the fortnightly chase does not
        # send one on every sweep.
        sa.Column("last_reminded_on", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("talk_id", "supervisor_id", name="uq_ehs_tbt_delivery"),
    )
    op.create_index("ix_ehs_tbt_deliveries_status", "ehs_tbt_deliveries", ["status"])

    op.create_table(
        "ehs_tbt_signatures",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("delivery_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_tbt_deliveries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        # Always present: employees get a name snapshot, and a contractor sitting
        # in on the meeting has nothing else.
        sa.Column("person_name", sa.String(255), nullable=False),
        sa.Column("person_role", sa.String(150), nullable=True),
        sa.Column("signature", sa.Text, nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_ehs_tbt_signatures_delivery", "ehs_tbt_signatures", ["delivery_id"])


def downgrade() -> None:
    op.drop_table("ehs_tbt_signatures")
    op.drop_table("ehs_tbt_deliveries")
    op.drop_table("ehs_toolbox_talks")
