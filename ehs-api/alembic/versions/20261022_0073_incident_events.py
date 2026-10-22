"""Timeline events on an incident: kind changed, reopened (with the reason).

Round 9: "log who changed it, and the date in the timeline" and "when reopening
an incident, require a mandatory reason … log who reopened it, the date and the
reason in the timeline." An incident already reopened gets its last reopening
as an event, with no reason: none was asked for then.

Revision ID: 20261022_0073
Revises: 20261020_0072
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261022_0073"
down_revision = "20261020_0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ehs_incident_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("incident_id", UUID(as_uuid=True),
                  sa.ForeignKey("ehs_incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("by_id", UUID(as_uuid=True), nullable=True),
        sa.Column("by_name", sa.String(255), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
    )
    op.create_index("ix_ehs_incident_events_incident_id", "ehs_incident_events", ["incident_id"])
    op.execute("""
        INSERT INTO ehs_incident_events (id, incident_id, kind, at, by_id, by_name, detail)
        SELECT gen_random_uuid(), id, 'reopened', reopened_at, reopened_by, reopened_by_name,
               NULL
          FROM ehs_incidents WHERE reopened_at IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_ehs_incident_events_incident_id", table_name="ehs_incident_events")
    op.drop_table("ehs_incident_events")
