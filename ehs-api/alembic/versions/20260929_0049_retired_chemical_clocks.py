"""Stop the SDS review clock on products no longer kept on site

WHMIS's three-year review is an obligation about a product that is on site. A
retired product has none, and `retire()` now discharges its sheets' clocks —
but the rows already written stay open, and they are the visible half of the
bug: "Needs attention" counts only active products while the compliance
calendar listed retired ones as overdue reviews. Two screens, one obligation,
two answers. Found on the dev database, where two of the overdue SDS reviews
belong to products marked retired.

This closes the ones already open, and completes their reminders. The evidence
note says why rather than leaving a silently satisfied clock — an auditor
reading the row has to be able to see that it was answered by the product
leaving, not by a review that never happened.

Deliberately not `satisfied_by`: nobody discharged these, the record did. The
column is nullable for exactly this case.

Revision ID: 20260929_0049
Revises: 20260929_0048
"""
from alembic import op
import sqlalchemy as sa

revision = "20260929_0049"
down_revision = "20260929_0048"
branch_labels = None
depends_on = None

NOTE = "Product no longer kept on site"


def upgrade() -> None:
    bind = op.get_bind()

    # The sheets of retired products whose review clock is still running.
    ids = bind.execute(sa.text("""
        SELECT d.id, d.source_id
          FROM ehs_statutory_deadlines d
          JOIN ehs_sds s ON s.id = d.source_id
          JOIN ehs_chemicals c ON c.id = s.chemical_id
         WHERE d.source_type = 'sds'
           AND d.satisfied_at IS NULL
           AND c.is_active IS FALSE
    """)).all()
    if not ids:
        return

    for deadline_id, sheet_id in ids:
        bind.execute(sa.text("""
            UPDATE ehs_statutory_deadlines
               SET satisfied_at = now(), evidence_note = :note
             WHERE id = :id
        """), {"id": deadline_id, "note": NOTE})
        # Its reminder, if the sweep had already raised one. Matched on the
        # document and the task type, the same pair `satisfy()` uses.
        bind.execute(sa.text("""
            UPDATE tasks
               SET is_completed = TRUE, completed_at = now()
             WHERE document_type = 'ehs_sds'
               AND document_id = :sheet
               AND type = 'ehs_statutory'
               AND is_completed IS FALSE
        """), {"sheet": sheet_id})


def downgrade() -> None:
    # Only the ones this migration closed are identifiable, by the note.
    op.execute(f"""
        UPDATE ehs_statutory_deadlines
           SET satisfied_at = NULL, evidence_note = NULL
         WHERE source_type = 'sds'
           AND evidence_note = '{NOTE}'
    """)
