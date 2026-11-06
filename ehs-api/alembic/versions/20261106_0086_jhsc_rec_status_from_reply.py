"""A backfilled state follows the reply, as a new reply now does.

0085 marked every answered recommendation Completed, while a new reply left the
state at New request. The rule now is crud/jhsc.state_after_reply: accepted with
its action closed is Completed; declined or deferred is New request; anything
else is Work in progress.

Only rows 0085 wrote are touched. A person choosing a state goes through the
change log and 0085 did not, so a Completed row with no logged state change is
one of 0085's.

Revision ID: 20261106_0086
Revises: 20261106_0085
"""
from alembic import op
import sqlalchemy as sa

revision = "20261106_0086"
down_revision = "20261106_0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    done = op.get_bind().execute(sa.text("""
        with backfilled as (
            select r.id,
                   case when r.outcome in ('declined', 'deferred') then 'new_request'
                        else 'in_progress' end as code
              from ehs_jhsc_recommendations r
              join ehs_vocabulary_items s on s.id = r.status_item_id
              left join ehs_actions a on a.id = r.action_id
             where s.code = 'completed'
               and not (r.outcome = 'accepted' and a.status = 'closed')
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
    """))
    print(f"0086: {done.rowcount} recommendation(s) moved to the state their reply gives")


def downgrade() -> None:
    # A state somebody may since have relied on is not taken away.
    pass
