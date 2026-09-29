"""Hang each JHSC reply task off its own recommendation

Found in review: `send_recommendation` closed the reply task with
`doc_id=meeting.id`, which is one document for every recommendation made at that
meeting. So sending a second recommendation closed the first one's task while its
twenty-one days were still running and nobody had answered it — the obligation
stayed, the reminder went.

The task now hangs off the recommendation. This moves the ones already raised.

★ Matched on the sequence number in the title, which is the only link the old
rows carry. A row whose title cannot be parsed, or whose (meeting, seq) matches
no recommendation, **aborts the migration with the row listed** rather than being
skipped or guessed at. Same rule as 0043: these are statutory records, and a
reply task pointing at the wrong recommendation is worse than one pointing at the
meeting.

`deadlines.py` already uses the recommendation as the document for the statutory
task, so both kinds now share an id and are told apart by `type` — which every
close in this module passes.

Revision ID: 20260929_0048
Revises: 20260929_0047
"""
from alembic import op
import sqlalchemy as sa

revision = "20260929_0048"
down_revision = "20260929_0047"
branch_labels = None
depends_on = None

TASK_TYPE = "ehs_jhsc_reply"
DOC_TYPE = "ehs_jhsc"


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(sa.text("""
        SELECT t.id, t.document_id, t.title
          FROM tasks t
         WHERE t.type = :task_type
           AND t.document_type = :doc_type
           AND EXISTS (SELECT 1 FROM ehs_jhsc_meetings m WHERE m.id = t.document_id)
    """), {"task_type": TASK_TYPE, "doc_type": DOC_TYPE}).all()

    unresolved: list[str] = []
    moves: list[tuple] = []

    for task_id, meeting_id, title in rows:
        # "Reply to JHSC recommendation #3" — the sequence is the only thing
        # tying the task to one of the meeting's recommendations.
        marker = (title or "").rsplit("#", 1)
        seq = None
        if len(marker) == 2 and marker[1].strip().isdigit():
            seq = int(marker[1].strip())
        if seq is None:
            unresolved.append(f"task {task_id}: no sequence number in {title!r}")
            continue

        rec_id = bind.execute(sa.text("""
            SELECT id FROM ehs_jhsc_recommendations
             WHERE meeting_id = :meeting_id AND seq = :seq
        """), {"meeting_id": meeting_id, "seq": seq}).scalar()
        if rec_id is None:
            unresolved.append(
                f"task {task_id}: meeting {meeting_id} has no recommendation #{seq}")
            continue
        moves.append((task_id, rec_id))

    if unresolved:
        raise RuntimeError(
            "Cannot move these JHSC reply tasks onto their recommendations, and "
            "guessing would point a statutory reminder at the wrong obligation. "
            "Resolve them by hand, then re-run:\n  " + "\n  ".join(unresolved))

    for task_id, rec_id in moves:
        bind.execute(sa.text("UPDATE tasks SET document_id = :rec WHERE id = :id"),
                     {"rec": rec_id, "id": task_id})


def downgrade() -> None:
    # Back onto the meeting, which is where they were. Lossless in the sense
    # that matters: the meeting is reachable from the recommendation.
    op.execute(f"""
        UPDATE tasks t
           SET document_id = r.meeting_id
          FROM ehs_jhsc_recommendations r
         WHERE t.document_id = r.id
           AND t.type = '{TASK_TYPE}'
           AND t.document_type = '{DOC_TYPE}'
    """)
