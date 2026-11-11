"""JHSC recommendations completed before HSE's verification existed.

★ B3/B4 re-review N1 (uniops-95): "completed" was read off the state, which
the people a recommendation was sent to may set by hand — so picking
Completed skipped HSE's verification (step 11) and left any review task open
for good. Completed now means HSE verified it, or it was already Completed
when step 11 arrived (0092). Those are marked here, once; a state picked
afterwards does not count.

Revision ID: 20261110_0097
Revises: 20261110_0096
"""
import sqlalchemy as sa
from alembic import op

revision = "20261110_0097"
down_revision = "20261110_0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ehs_jhsc_recommendations", sa.Column(
        "completed_before_verification", sa.Boolean, nullable=False, server_default=sa.false()))
    marked = op.get_bind().execute(sa.text("""
        update ehs_jhsc_recommendations r set completed_before_verification = true
        from ehs_vocabulary_items v
        where v.id = r.status_item_id and v.vocabulary_code = 'jhsc_rec_status'
          and v.code = 'completed' and r.hse_verified_at is null""")).rowcount
    print(f"jhsc recommendations completed before step 11: {marked}")


def downgrade() -> None:
    op.drop_column("ehs_jhsc_recommendations", "completed_before_verification")
