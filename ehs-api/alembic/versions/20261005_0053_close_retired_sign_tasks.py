"""Close the signature tasks left on retired document versions

Round 5, TC-22: "policy documents that are Superseded by a later version still
show up in the My actions list to sign and yet when it is opened there is no way
to sign the superseeded document."

The cause was fixed in the code — publishing a new version now retires the old
one through `retire()`, which closes its outstanding read-and-sign tasks. That
only helps from now on. The tasks already raised are still in people's inboxes,
including the ones the person who reported this is looking at.

★ Narrow on purpose, and the narrowing is the whole safety of it. Only tasks
that are all of: `ehs_doc`, `ehs_read_sign`, not completed, and against a
document whose status is `retired`. A task against a *published* document is
somebody's real outstanding obligation and must not be touched — that is the
difference between clearing stale work and clearing work.

★ `tasks` is the shared inbox table, written by every module. Scoped to
`document_type = 'ehs_doc'` so nothing outside Safety is in range, and the count
is printed so the release log says what was closed rather than "it ran".

Nobody is recorded as having completed them: `completed_by` stays null. They were
not done, they stopped being owed — and a name there would say somebody signed a
document they never saw.

Revision ID: 20261005_0053
Revises: 20261005_0052
"""
from alembic import op
import sqlalchemy as sa

revision = "20261005_0053"
down_revision = "20261005_0052"
branch_labels = None
depends_on = None

DOC_TYPE = "ehs_doc"
TASK_TYPE = "ehs_read_sign"


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(sa.text("""
        SELECT t.id, t.title, d.doc_no, d.version
          FROM tasks t
          JOIN ehs_documents d ON d.id = t.document_id
         WHERE t.document_type = :doc_type
           AND t.type = :task_type
           AND t.is_completed IS FALSE
           AND d.status = 'retired'
    """), {"doc_type": DOC_TYPE, "task_type": TASK_TYPE}).mappings().all()

    if not rows:
        print("0053: no signature tasks on retired versions; nothing to close")
        return

    for row in rows:
        print(f"0053: closing {row['title']!r} on {row['doc_no']} v{row['version']}")

    bind.execute(sa.text("""
        UPDATE tasks t
           SET is_completed = TRUE, completed_at = now()
         WHERE t.id IN :ids
    """).bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": [r["id"] for r in rows]})
    print(f"0053: closed {len(rows)} signature task(s) on retired versions")


def downgrade() -> None:
    # ★ Not reopened. Which of the completed tasks on a retired version this
    # migration closed is not recorded — `completed_by` is null on the ones it
    # touched, but it is also null on anything a sweep closed — and reopening
    # the wrong ones would put a signature request back in front of somebody for
    # a document they are not allowed to sign. The forward step is safe to
    # repeat; this one is deliberately a no-op.
    pass
