"""First aid is recorded on the injury form, not in a register beside it.

Two places recorded the same event. A first aid treatment is an incident of
injury class `first_aid`, and keeping a separate register meant the same cut
finger was entered twice or — far more often — once, in whichever of the two
the person happened to open. HSE asked for the register to be part of the
injured-worker form.

★ The existing register rows are NOT migrated. That is the decision, and the
reason is that they are the Regulation 1101 record as it stands: rewriting
twelve statutory entries into incidents would change their numbers, their
dates of record and their authorship to make a screen tidier. They stay, the
register still lists them, and new treatment is recorded on the incident.

So the register's listing has to read both from here on, or the statutory
register would quietly stop being complete at the moment of this change —
which would be a compliance regression caused by a usability fix.

What the incident was missing to hold a treatment: whether the person was sent
off site, and what follow-up was owed. Both go on the injured person rather
than the incident, because an incident can hurt two people differently.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260923_0035" is 13 characters.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260923_0035"
down_revision = "20260922_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_incident_persons",
                  sa.Column("sent_offsite", sa.Boolean(), nullable=True))
    op.add_column("ehs_incident_persons",
                  sa.Column("follow_up", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_incident_persons", "follow_up")
    op.drop_column("ehs_incident_persons", "sent_offsite")
