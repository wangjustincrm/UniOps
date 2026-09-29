"""Put the new "where injuries are happening" panel on layouts already saved

Round 4, TC-01: "where it happened is part of the incident creation record, but
that location is not accurate in the dashboard where people are getting hurt
graph."

The panel by that name was grouped by department, and an incident's department
is the department of the person who was hurt — so the area the reporter chose
appeared on the dashboard nowhere, and on the dev database the largest bar in it
was "Not recorded". A panel that reads the location now exists.

★ Anybody who has arranged their dashboard has a stored list of panel keys, and
a key that is not in it is not drawn. So the person who reported this would have
had it fixed and still not seen it. Appended once, here, rather than merged in
on every read: a panel somebody later unticks has to stay unticked, and a read
that keeps re-adding what is missing would make their choice impossible to keep
— the same fault as a default value that reasserts itself.

Revision ID: 20260929_0047
Revises: 20260929_0046
"""
from alembic import op

revision = "20260929_0047"
down_revision = "20260929_0046"
branch_labels = None
depends_on = None

PANEL = "injured_locations"
# Immediately after the department cut it replaces, where one is present, so the
# two sit together and the difference between them is visible.
NEIGHBOUR = "injured_departments"


def upgrade() -> None:
    # Appended after the department panel when that is on the layout, otherwise
    # at the end. Written as SQL rather than in Python so it touches every row
    # in one statement and needs no model import.
    op.execute(f"""
        UPDATE ehs_dashboard_layouts
           SET panels = (
                 CASE
                   WHEN panels ? '{NEIGHBOUR}' THEN (
                     SELECT jsonb_agg(p ORDER BY n)
                       FROM (
                         SELECT value AS p, ordinality * 2 AS n
                           FROM jsonb_array_elements(panels) WITH ORDINALITY AS t(value, ordinality)
                         UNION ALL
                         SELECT to_jsonb('{PANEL}'::text),
                                (SELECT ordinality * 2 + 1
                                   FROM jsonb_array_elements(panels) WITH ORDINALITY AS u(value, ordinality)
                                  WHERE value = to_jsonb('{NEIGHBOUR}'::text)
                                  LIMIT 1)
                       ) AS ordered
                   )
                   ELSE panels || to_jsonb('{PANEL}'::text)
                 END
               )
         WHERE jsonb_typeof(panels) = 'array'
           AND NOT panels ? '{PANEL}'
    """)


def downgrade() -> None:
    op.execute(f"""
        UPDATE ehs_dashboard_layouts
           SET panels = (
                 SELECT coalesce(jsonb_agg(value), '[]'::jsonb)
                   FROM jsonb_array_elements(panels)
                  WHERE value <> to_jsonb('{PANEL}'::text)
               )
         WHERE jsonb_typeof(panels) = 'array'
           AND panels ? '{PANEL}'
    """)
