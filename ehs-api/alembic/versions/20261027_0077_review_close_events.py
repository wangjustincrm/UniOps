"""Every HSE review and every close on the incident timeline.

Round 10, TC-08: "when the reopened incident has the HSE review completed, or
the incident is closed again, record both under the timeline." From now on each
is an ehs_incident_events row. The review and the close an incident carries
today are backfilled as rows; earlier ones, cleared by a reopening, were not
kept anywhere and cannot be.

Revision ID: 20261027_0077
Revises: 20261023_0076
"""
from alembic import op

revision = "20261027_0077"
down_revision = "20261023_0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO ehs_incident_events (id, incident_id, kind, at, by_id, by_name, detail)
        SELECT gen_random_uuid(), id, 'hse_reviewed', hse_reviewed_at, hse_reviewed_by,
               hse_reviewed_by_name, NULL
          FROM ehs_incidents WHERE hse_reviewed_at IS NOT NULL
    """)
    op.execute("""
        INSERT INTO ehs_incident_events (id, incident_id, kind, at, by_id, by_name, detail)
        SELECT gen_random_uuid(), id, 'closed', closed_at, NULL, NULL, NULL
          FROM ehs_incidents WHERE closed_at IS NOT NULL AND status = 'closed'
    """)


def downgrade() -> None:
    op.execute("DELETE FROM ehs_incident_events WHERE kind IN ('hse_reviewed', 'closed')")
