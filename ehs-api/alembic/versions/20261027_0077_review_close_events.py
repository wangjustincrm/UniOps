"""Every HSE review and every close on the incident timeline.

Round 10, TC-08: "when the reopened incident has the HSE review completed, or
the incident is closed again, record both under the timeline." From now on each
is an ehs_incident_events row. The review and the close an incident carries
today are backfilled as rows (once: the downgrade keeps them, so a
re-upgrade skips any already there); earlier ones, cleared by a reopening, were not
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
        SELECT gen_random_uuid(), i.id, 'hse_reviewed', hse_reviewed_at, hse_reviewed_by,
               hse_reviewed_by_name, NULL
          FROM ehs_incidents i WHERE hse_reviewed_at IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM ehs_incident_events e WHERE e.incident_id = i.id
                            AND e.kind = 'hse_reviewed'
                            AND e.at >= i.hse_reviewed_at - interval '1 minute')
    """)
    op.execute("""
        INSERT INTO ehs_incident_events (id, incident_id, kind, at, by_id, by_name, detail)
        SELECT gen_random_uuid(), i.id, 'closed', closed_at, NULL, NULL, NULL
          FROM ehs_incidents i WHERE closed_at IS NOT NULL AND status = 'closed'
           AND NOT EXISTS (SELECT 1 FROM ehs_incident_events e WHERE e.incident_id = i.id
                            AND e.kind = 'closed'
                            AND e.at >= i.closed_at - interval '1 minute')
    """)


def downgrade() -> None:
    # Nothing to take out. The upgrade changes no schema, and its backfilled
    # rows look exactly like the reviews and closes recorded since, which are
    # the only record of those left once an incident is reopened. Deleting by
    # kind would take those too; the earlier code reads the rows fine.
    pass
