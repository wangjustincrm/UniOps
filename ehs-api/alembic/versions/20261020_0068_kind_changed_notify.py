"""Tell the reporter and their supervisor when an incident's kind changes.

Round 8, TC-13: "add a notification that the incident class has changed and
email it." A new list under Settings → Who is told, "Kind of incident
changed", beside the other status changes. It starts with the reporter and
their supervisor ticked, as "Investigation started" and "Closed" did in 0059
— HSE can untick them or name people there. An anonymous report copies
neither, as for every list.

Revision ID: 20261020_0068
Revises: 20261016_0067
"""
from alembic import op

revision = "20261020_0068"
down_revision = "20261016_0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE ehs_config
        SET incident_notify_include = coalesce(incident_notify_include, '{}'::jsonb)
            || '{"status:kind_changed": {"reporter": true, "supervisor": true}}'::jsonb
        WHERE NOT (coalesce(incident_notify_include, '{}'::jsonb) ? 'status:kind_changed')
    """)


def downgrade() -> None:
    op.execute("UPDATE ehs_config SET incident_notify_include = incident_notify_include - 'status:kind_changed'")
