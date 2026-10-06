"""What was found, on a hazard

Round 5, answer box 11: "the what was found, immediate causes and root cause, and
corrective actions all need to be added ot the hazard register form."

The causes came with migration 0054. This is the narrative half: an incident
investigation records `sequence_of_events` under the heading "What happened", and
a hazard had nowhere to say what was actually found when somebody went and
looked.

Deliberately one field, not a copy of the investigation's four. A hazard already
carries its own description, its existing controls, and a risk assessment with
before-and-after ratings; adding an investigator, supporting investigators and a
root-cause narrative on top would be building an investigation form on a register
entry. What was missing is the finding.

Revision ID: 20261006_0055
Revises: 20261006_0054
"""
from alembic import op
import sqlalchemy as sa

revision = "20261006_0055"
down_revision = "20261006_0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_hazards", sa.Column("findings", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_hazards", "findings")
