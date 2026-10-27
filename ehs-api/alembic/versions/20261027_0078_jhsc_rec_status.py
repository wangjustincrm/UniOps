"""A list of states for JHSC recommendations, and the state each one is in.

Round 10, TC-11: "add jhsc task list in the settings/lists registry for
picking the current state of recommendations. add 4 states. for example a JHSC
recommendation might be to replace a eye wash and shower station, the request
would have a state of new request. the work needs to be reviewed by
maintenance, etc." Four to start with; HSE keeps the list under Settings →
Lists. The recommendation keeps the label it was given, like every other
pick from a list.

Revision ID: 20261027_0078
Revises: 20261027_0077
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261027_0078"
down_revision = "20261027_0077"
branch_labels = None
depends_on = None

_NS = uuid.UUID("6f3b7c3a-1d6e-4c55-9b9e-0b5f0f1c2a69")
_STATES = [
    ("new_request", "New request"),
    ("maintenance_review", "Under review by maintenance"),
    ("in_progress", "Work in progress"),
    ("completed", "Completed"),
]


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        insert into ehs_vocabularies (code, name, description, is_hierarchical, is_system_locked)
        values ('jhsc_rec_status', 'JHSC recommendation status',
                'Where the work on a JHSC recommendation stands. Picked on the recommendation.',
                false, false)
        on conflict (code) do nothing
    """))
    for sort, (code, label) in enumerate(_STATES, start=1):
        conn.execute(sa.text("""
            insert into ehs_vocabulary_items
                (id, vocabulary_code, parent_id, code, label, sort_order, is_active, attrs, path)
            values (:id, 'jhsc_rec_status', null, :code, :label, :sort, true, '{}'::jsonb, :label)
            on conflict (vocabulary_code, code) do nothing
        """), {"id": uuid.uuid5(_NS, f"jhsc_rec_status:{code}"), "code": code,
               "label": label, "sort": sort})
    op.add_column("ehs_jhsc_recommendations",
                  sa.Column("status_item_id", UUID(as_uuid=True), nullable=True))
    op.add_column("ehs_jhsc_recommendations",
                  sa.Column("status_label", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("ehs_jhsc_recommendations", "status_label")
    op.drop_column("ehs_jhsc_recommendations", "status_item_id")
    op.execute("delete from ehs_vocabulary_items where vocabulary_code = 'jhsc_rec_status'")
    op.execute("delete from ehs_vocabularies where code = 'jhsc_rec_status'")
