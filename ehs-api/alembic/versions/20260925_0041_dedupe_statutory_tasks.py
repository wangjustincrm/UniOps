"""One statutory obligation, one open task.

The sweep raised a new task each time a deadline crossed an escalation
threshold instead of rewording the one already there, so a single obligation
that went from "due in 23h" to "due in 3h" to "overdue" left three open tasks
saying the same thing in three tones. On the test database thirty-six open
tasks stood for twelve incidents, one of them holding six.

Nothing noticed because nothing read tasks assigned to a role rather than to a
person; they reached no screen. The moment that was fixed they became most of
what the HSE Manager could see.

This keeps the newest task per obligation — the newest carries the current
wording and priority — and completes the rest. Completing rather than deleting:
these rows are the record of what the inbox said, and the audit trail reads
them.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260925_0041" is 13 characters.
"""
from alembic import op

revision = "20260925_0041"
down_revision = "20260925_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Grouped by document *and* by the obligation's name, which leads the
    # title: one record can owe both a Ministry report and a WSIB form, and
    # those are two obligations rather than a duplicate.
    op.execute("""
        with ranked as (
            select id,
                   row_number() over (
                     partition by document_type, document_id,
                                  split_part(title, ' — ', 1)
                     order by created_at desc, id desc) as rn
              from tasks
             where type = 'ehs_statutory'
               and is_completed = false
        )
        update tasks t
           set is_completed = true,
               completed_at = now()
          from ranked r
         where r.id = t.id
           and r.rn > 1
    """)


def downgrade() -> None:
    # Reopening them would put the duplicates back on somebody's list. The
    # rows are still there, marked complete, which is the honest record of
    # what happened.
    pass
