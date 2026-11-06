"""Do what 0086 meant to do.

0086 changed nothing. With no action the left join gives a NULL status, and
"not (accepted and NULL)" is NULL, so the very rows it was for were filtered
out. The rule is unchanged: see 0086 and crud/jhsc.state_after_reply.

Revision ID: 20261106_0087
Revises: 20261106_0086
"""
from alembic import op
import sqlalchemy as sa

revision = "20261106_0087"
down_revision = "20261106_0086"
branch_labels = None
depends_on = None

# Module level so a test can run it. Only rows 0085 wrote: a person choosing a
# state goes through the change log, and 0085 did not.
STATE_FROM_REPLY = """
    with backfilled as (
        select r.id,
               case when r.outcome in ('declined', 'deferred') then 'new_request'
                    else 'in_progress' end as code
          from ehs_jhsc_recommendations r
          join ehs_vocabulary_items s on s.id = r.status_item_id
          left join ehs_actions a on a.id = r.action_id
         where s.code = 'completed'
           and not (r.outcome is not distinct from 'accepted'
                    and a.status is not distinct from 'closed')
           and not exists (
               select 1 from ehs_change_log l
                where l.table_name = 'ehs_jhsc_recommendations'
                  and l.row_id = r.id::text and l.field = 'status_item_id'))
    update ehs_jhsc_recommendations r
       set status_item_id = i.id, status_label = i.label
      from backfilled b
      join ehs_vocabulary_items i
        on i.vocabulary_code = 'jhsc_rec_status' and i.code = b.code
     where r.id = b.id
"""


def upgrade() -> None:
    done = op.get_bind().execute(sa.text(STATE_FROM_REPLY))
    print(f"0087: {done.rowcount} recommendation(s) moved to the state their reply gives")


def downgrade() -> None:
    # A state somebody may since have relied on is not taken away.
    pass
