"""Section B — the injury detail WSIB needs, in the order a first aider asks it.

HSE sent a completed Medical form on 31 August, which is the first sight anyone
has had of Section B filled in. Its three subsections are reproduced here field
for field.

The Medical Information block is not an arbitrary set of questions: signs,
allergies, medicines, past history, last meal, events prior is **SAMPLE**, the
standard history a first aider is trained to take in that order. The column
order matches, and the form must render them in that order too — a first aider
reciting the mnemonic down a re-sorted screen will skip one.

A separate table rather than columns on `ehs_incidents`, for two reasons. It is
health information — allergies, medication, medical history — which PHIPA treats
differently from the rest of an incident record, and HSE's own form is headed
`Private`. A separate table is what makes `ehs.incident.medical.read` a real
boundary rather than a field-level convention that the next endpoint forgets.
Second, most incidents have no Section B at all: it is filled only when there is
an injury, so putting fifteen usually-null columns on the main table would make
every incident read as if it were missing something.

`timeline` is JSONB because it is a repeating pair (time, event) with no
identity of its own, queried only as part of this record.

Revision ID: 20260831_0007
Revises: 20260831_0006
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260831_0007"
down_revision = "20260831_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_incident_medical",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("incident_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_incidents.id", ondelete="CASCADE"),
                  nullable=False, unique=True),

        sa.Column("supervisor_name", sa.String(255), nullable=True),
        sa.Column("type_of_injury", sa.String(255), nullable=True),

        # ── Medical Information — SAMPLE order, do not re-sort ──────────────
        sa.Column("assessed_at_text", sa.String(50), nullable=True),
        sa.Column("signs_symptoms", sa.Text, nullable=True),
        sa.Column("allergies", sa.Text, nullable=True),
        sa.Column("medicines", sa.Text, nullable=True),
        sa.Column("past_medical_history", sa.Text, nullable=True),
        sa.Column("last_meal", sa.Text, nullable=True),
        sa.Column("events_prior", sa.Text, nullable=True),
        sa.Column("general_comments", sa.Text, nullable=True),

        # ── Vitals ─────────────────────────────────────────────────────────
        sa.Column("vitals_at_text", sa.String(50), nullable=True),
        sa.Column("pulse", sa.String(20), nullable=True),
        sa.Column("respirations", sa.String(20), nullable=True),
        sa.Column("blood_pressure", sa.String(20), nullable=True),
        sa.Column("pain_level", sa.SmallInteger, nullable=True),
        sa.Column("pain_location", sa.String(255), nullable=True),
        sa.Column("vitals_other", sa.Text, nullable=True),

        # [{"time": "10:06 am", "event": "…"}] — a repeating pair with no
        # identity of its own.
        sa.Column("timeline", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),

        sa.Column("recorded_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recorded_by_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("ehs_incident_medical")
