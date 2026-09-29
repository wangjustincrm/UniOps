"""A submission knows what record it belongs to

Round 4, TC-11: "press the open the minutes returns a new form not the same
form back."

It returns the same form — the meeting points at one submission and the second
press opens that one. What it does not do is *say so*. The form opens showing
its own number and nothing else, so a set of minutes that has not been filled
in yet is indistinguishable from a fresh blank form, and the only way to tell
was to compare reference numbers between two screens.

The link existed in one direction only: the meeting knew its minutes, the
minutes knew nothing. So a submission now records what it is for, and the
backfill reads the direction that already exists.

Revision ID: 20260929_0046
Revises: 20260925_0045
"""
from alembic import op
import sqlalchemy as sa

revision = "20260929_0046"
down_revision = "20260925_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_form_submissions",
                  sa.Column("source_type", sa.String(20), nullable=True))
    op.add_column("ehs_form_submissions",
                  sa.Column("source_id", sa.dialects.postgresql.UUID(as_uuid=True),
                            nullable=True))
    # The reference as it read when the link was made, for the same reason every
    # other record here keeps a label snapshot: the number has to stay printable
    # after the meeting row is gone.
    op.add_column("ehs_form_submissions",
                  sa.Column("source_ref", sa.String(50), nullable=True))
    op.create_index("ix_ehs_submission_source", "ehs_form_submissions",
                    ["source_type", "source_id"])

    # Backfill from the direction that already exists. Only committee minutes
    # have a link today; equipment checks and scheduled runs reach their
    # submission through their own tables and will be filled in as they are
    # created.
    op.execute("""
        UPDATE ehs_form_submissions s
           SET source_type = 'jhsc',
               source_id   = m.id,
               source_ref  = m.meeting_no
          FROM ehs_jhsc_meetings m
         WHERE m.minutes_submission_id = s.id
           AND s.source_type IS NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_ehs_submission_source", table_name="ehs_form_submissions")
    op.drop_column("ehs_form_submissions", "source_ref")
    op.drop_column("ehs_form_submissions", "source_id")
    op.drop_column("ehs_form_submissions", "source_type")
