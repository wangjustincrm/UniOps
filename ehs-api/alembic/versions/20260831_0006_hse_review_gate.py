"""Closing an incident, and the HSE review that has to happen first.

HSE's instruction, written on the Section C row of the form structure they
returned: "an investigation is required on all incidents where a worker was
injured or equipment was damaged … all incidents for injuries and damaged
equipment must be reviewed by HSE before they can be marked as closed."

Two things were missing to honour that. There was no way to close an incident at
all — `closed_at` existed on every EHS table and nothing ever set it, so every
incident ever filed is still open — and there was nowhere to record that HSE had
looked at one.

The review is a separate act from the investigation, and separate from closing.
A supervisor can investigate; only HSE reviews; either can then close. Collapsing
review into close would mean the person closing is recorded as the reviewer, and
the whole point is that a second pair of eyes saw it.

The columns follow the module's snapshot rule — reviewer id with ON DELETE SET
NULL plus the name copied at review time, so a leaver does not erase the fact
that the review happened.

Revision ID: 20260831_0006
Revises: 20260831_0005
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20260831_0006"
down_revision = "20260831_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_incidents",
                  sa.Column("hse_reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ehs_incidents",
                  sa.Column("hse_reviewed_by", UUID(as_uuid=True),
                            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("ehs_incidents",
                  sa.Column("hse_reviewed_by_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_incidents", "hse_reviewed_by_name")
    op.drop_column("ehs_incidents", "hse_reviewed_by")
    op.drop_column("ehs_incidents", "hse_reviewed_at")
