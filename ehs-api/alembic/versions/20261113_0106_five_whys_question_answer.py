"""Each of the 5 Whys becomes a question and its answer (UAT 13, TC-06).

What was stored as one line per step was the answer; it is kept as the answer,
with the question left for the investigator to fill in.

Revision ID: 20261113_0106
Revises: 20261113_0105
"""
from alembic import op

revision = "20261113_0106"
down_revision = "20261113_0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE ehs_incident_investigations i
           SET five_whys = (
               SELECT coalesce(jsonb_agg(
                   CASE WHEN jsonb_typeof(w) = 'string'
                        THEN jsonb_build_object('question', '', 'answer', w #>> '{}')
                        ELSE w END ORDER BY n), '[]'::jsonb)
                 FROM jsonb_array_elements(i.five_whys) WITH ORDINALITY AS t(w, n))
         WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(i.five_whys) w
                        WHERE jsonb_typeof(w) = 'string')
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE ehs_incident_investigations i
           SET five_whys = (
               SELECT coalesce(jsonb_agg(w ->> 'answer' ORDER BY n), '[]'::jsonb)
                 FROM jsonb_array_elements(i.five_whys) WITH ORDINALITY AS t(w, n))
    """)
