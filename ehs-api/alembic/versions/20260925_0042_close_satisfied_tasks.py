"""Close the prompts for obligations that were already discharged.

`satisfy()` recorded that the report had been filed and the evidence uploaded,
and left the inbox task open. Nothing else closed one either, so every
statutory task ever raised was still open: HSE could file with the Ministry and
be told to do it, for good.

Invisible until this round, because nothing read tasks assigned to a role
rather than to a person. Once that was fixed these were most of what the HSE
Manager could see.

Matched on the obligation's name, which leads the task title, so an incident
that owes both a Ministry report and a WSIB form keeps the prompt for whichever
is still outstanding. Completed rather than deleted: the row is the record of
what the inbox said.

Revision id length: alembic_version_ehs.version_num is varchar(32);
"20260925_0042" is 13 characters.
"""
from alembic import op

revision = "20260925_0042"
down_revision = "20260925_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        update tasks t
           set is_completed = true,
               completed_at = coalesce(d.satisfied_at, now())
          from ehs_statutory_deadlines d
         where t.type = 'ehs_statutory'
           and t.is_completed = false
           and t.document_id = d.source_id
           and d.satisfied_at is not null
           and split_part(t.title, ' — ', 1) = replace(d.kind, '_', ' ')
    """)


def downgrade() -> None:
    # Reopening a prompt for an obligation that has been discharged would be
    # telling somebody to do something they have already done.
    pass
