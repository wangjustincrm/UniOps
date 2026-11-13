"""Close reply tasks on JHSC recommendations that were already answered.

UAT 13: re-sending an answered recommendation opened a reply task nobody could
complete (Ken Boucher, JHSC-2026-0001 #2). The send no longer does; this closes
the ones it left.

Revision ID: 20261113_0105
Revises: 20261111_0104
"""
from alembic import op

revision = "20261113_0105"
down_revision = "20261111_0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE tasks t SET is_completed = true, completed_at = now()
        FROM ehs_jhsc_recommendations r
        WHERE t.type = 'ehs_jhsc_reply' AND NOT t.is_completed
          AND t.document_type = 'ehs_jhsc' AND t.document_id = r.id
          AND r.responded_at IS NOT NULL
    """)


def downgrade() -> None:
    pass  # what was closed was never completable; nothing to reopen
