"""Who is told can include the reporter and the reporter's supervisor.

Round 6, TC-07: "rather than email a specific worker, additionally create a
checkbox on all 'who is told' items to include the requester and the
requester's supervisor." And from the same round's answers: "when there are
status changes on the incident (investigation, closed) notify the reporter and
their supervisor. Make this a rule that can be modified down the road."

{list key: {"reporter": bool, "supervisor": bool}} — the list key being an
incident type, "*" for any incident, or "status:<event>" for a status change.
The two changes HSE named start ticked; everything else starts unticked, which
is what it did before.

Revision ID: 20261013_0059
Revises: 20261008_0058
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261013_0059"
down_revision = "20261008_0058"
branch_labels = None
depends_on = None

_STARTS_TICKED = ('{"status:under_investigation": {"reporter": true, "supervisor": true},'
                  ' "status:closed": {"reporter": true, "supervisor": true}}')


def upgrade() -> None:
    op.add_column("ehs_config", sa.Column(
        "incident_notify_include", JSONB, nullable=False, server_default="{}"))
    op.execute(f"UPDATE ehs_config SET incident_notify_include = '{_STARTS_TICKED}'::jsonb")


def downgrade() -> None:
    op.drop_column("ehs_config", "incident_notify_include")
