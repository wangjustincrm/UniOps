"""Close the review deadlines of document versions no longer in force.

Found building round 7's archive (TC-11): retiring a version — archiving it, or
publishing the one that replaces it — closed its read-and-sign tasks but left
its annual-review deadline open, so it would run overdue and escalate about a
version nobody may use. The code now closes it on retiring; this closes the
ones already left behind (11 on dev3), with the review reminder task each may
have raised.

Revision ID: 20261016_0066
Revises: 20261013_0065
"""
from alembic import op

revision = "20261016_0066"
down_revision = "20261013_0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE tasks t SET is_completed = true, completed_at = now()
        FROM ehs_statutory_deadlines d
        JOIN ehs_documents doc ON doc.id = d.source_id
        WHERE d.source_type = 'document' AND doc.status = 'retired'
          AND d.satisfied_at IS NULL
          AND t.document_type = 'ehs_doc' AND t.document_id = doc.id
          AND t.type = 'ehs_statutory' AND NOT t.is_completed
    """)
    op.execute("""
        UPDATE ehs_statutory_deadlines d
        SET satisfied_at = now(),
            evidence_note = 'This version is no longer in force; nothing to review'
        FROM ehs_documents doc
        WHERE doc.id = d.source_id AND d.source_type = 'document'
          AND doc.status = 'retired' AND d.satisfied_at IS NULL
    """)


def downgrade() -> None:
    # Not reversed: which of these were open before cannot be told apart from
    # ones closed by hand, and reopening a deadline on a retired version would
    # only bring the fault back.
    pass
