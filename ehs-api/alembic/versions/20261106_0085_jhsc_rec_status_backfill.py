"""Every JHSC recommendation has a state.

Round 11, TC-06: "this status needs to be added to all recommendations." 0078
added the list but gave a state only to recommendations made after it. One
already answered is Completed; any other is a New request.

Revision ID: 20261106_0085
Revises: 20261029_0084
"""
from alembic import op
import sqlalchemy as sa

revision = "20261106_0085"
down_revision = "20261029_0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    done = op.get_bind().execute(sa.text("""
        update ehs_jhsc_recommendations r
           set status_item_id = i.id, status_label = i.label
          from ehs_vocabulary_items i
         where r.status_item_id is null
           and i.vocabulary_code = 'jhsc_rec_status'
           and i.code = case when r.responded_at is not null
                             then 'completed' else 'new_request' end
    """))
    print(f"0085: {done.rowcount} recommendation(s) given a state")


def downgrade() -> None:
    # A state somebody may since have relied on is not taken away.
    pass
