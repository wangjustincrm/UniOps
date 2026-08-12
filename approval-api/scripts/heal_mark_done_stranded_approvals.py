"""Heal approval documents stranded by the Task Inbox "Mark Done" button.

Background
----------
epms-api's `POST /tasks/{id}/complete` flipped ANY task to completed — including
`approve_*` tasks — without going through the approval engine. Clicking "Mark Done"
on an approval task therefore closed the document's ONLY open approve task while the
document itself stayed `submitted`/`in_review`. With zero open approve tasks the
Approve button disappears for everyone (it is gated purely on holding an open
approve task, and `system_admin` sees "all tasks" = nothing), so the document can
never move again. The button is removed in `fix/epms-remove-mark-done`; this script
heals the documents it already stranded.

Detection (evidence, not guessing)
----------------------------------
A genuine approval writes an ApprovalEvent in the SAME transaction that completes
the task. So a candidate is an `approve_*` task that is:
  * completed, with `completed_by` set (a human clicked something), AND
  * has NO approve/reject/return ApprovalEvent on that document within ±2 min of
    `completed_at`, AND
  * whose document currently has ZERO open approve tasks.
Documents already in a terminal state (approved / issued / paid / cancelled …) are
left alone — `_resync_document` no-ops on them.

Heal
----
`engine._resync_document()` — exactly what `POST /approval/v1/routing/resync-document`
runs. For a doc with no open task it re-issues the approve task for the document's
stored step (`reissue step<N> -> <role>`). Idempotent: re-running finds nothing.

Usage
-----
    docker compose -f docker-compose.prod.yml exec approval-api \
        python -m scripts.heal_mark_done_stranded_approvals --allow-production
    # review the plan, then:
    docker compose -f docker-compose.prod.yml exec approval-api \
        python -m scripts.heal_mark_done_stranded_approvals --allow-production --apply

Dry-run by DEFAULT (rolls back). Must be run as a module (`python -m scripts.…`)
from WORKDIR=/app, otherwise `app` is not importable.
"""
import argparse
import asyncio
import os
import sys
import uuid

from sqlalchemy import select, text

from app.crud.engine import _resolve_meta, _resync_document, _status_of
from app.db.base import AsyncSessionLocal

# Documents whose only approve task was closed with no matching approval event,
# and which have no open approve task left.
DETECT_SQL = text("""
    SELECT DISTINCT ON (t.document_type, t.document_id)
           t.document_type, t.document_id, t.document_number,
           t.assigned_role, t.completed_at, u.email AS completed_by_email
    FROM tasks t
    LEFT JOIN users u ON u.id = t.completed_by
    WHERE t.type LIKE 'approve%'
      AND t.is_completed
      AND t.completed_by IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM approval_events e
          WHERE e.document_id = t.document_id
            AND e.action IN ('approve', 'reject', 'return')
            AND e.created_at BETWEEN t.completed_at - interval '2 minutes'
                                 AND t.completed_at + interval '2 minutes')
      AND NOT EXISTS (
          SELECT 1 FROM tasks o
          WHERE o.document_id = t.document_id
            AND o.type LIKE 'approve%'
            AND NOT o.is_completed)
    ORDER BY t.document_type, t.document_id, t.completed_at DESC
""")

HEALABLE_STATUSES = ("submitted", "in_review")


async def main(apply: bool) -> int:
    healed, skipped, errors = [], [], []

    async with AsyncSessionLocal() as db:
        candidates = (await db.execute(DETECT_SQL)).mappings().all()
        print(f"Candidates (approve task closed with no approval event, no open task): "
              f"{len(candidates)}\n")

        for c in candidates:
            doc_type = c["document_type"]
            doc_id = c["document_id"]
            label = f"{doc_type.upper()} {c['document_number']}"
            who = c["completed_by_email"] or "?"
            when = c["completed_at"]

            try:
                meta = _resolve_meta(doc_type)
            except Exception as exc:  # doc types the engine doesn't route
                skipped.append((label, f"unsupported doc_type: {type(exc).__name__}"))
                continue

            doc = (await db.execute(
                select(meta["model"]).where(meta["model"].id == doc_id)
            )).scalar_one_or_none()
            if doc is None:
                skipped.append((label, "document row not found"))
                continue

            status = _status_of(meta, doc)
            if status not in HEALABLE_STATUSES:
                # Terminal / already recovered — nothing stranded.
                skipped.append((label, f"status={status} (not stranded)"))
                continue

            print(f"  {label:26s} step={doc.approval_step_idx} status={status}")
            print(f"      closed by {who} at {when} (role={c['assigned_role']})")

            try:
                # SAVEPOINT per document: one bad doc can't poison the batch.
                async with db.begin_nested():
                    summary = await _resync_document(db, doc_type, uuid.UUID(str(doc_id)))
            except Exception as exc:
                errors.append((label, f"{type(exc).__name__}: {exc}"))
                print(f"      ! {type(exc).__name__}: {exc}")
                continue

            if summary is None:
                skipped.append((label, "resync produced no change"))
                print("      -> no change")
            else:
                healed.append((label, "; ".join(summary["actions"])))
                print(f"      -> {'; '.join(summary['actions'])}")

        print()
        for label, reason in skipped:
            print(f"  skip {label}: {reason}")

        if apply:
            await db.commit()
            print(f"\nAPPLIED: healed {len(healed)} document(s); "
                  f"{len(skipped)} skipped; {len(errors)} error(s).")
        else:
            await db.rollback()
            print(f"\nDRY RUN (rolled back): would heal {len(healed)} document(s); "
                  f"{len(skipped)} skipped; {len(errors)} error(s). "
                  f"Re-run with --apply to commit.")

    return 1 if errors else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="commit (default: dry-run)")
    ap.add_argument("--allow-production", action="store_true",
                    help="required when ENVIRONMENT=production")
    args = ap.parse_args()

    if os.getenv("ENVIRONMENT", "").lower() == "production" and not args.allow_production:
        sys.exit("Refusing to run against production without --allow-production")

    sys.exit(asyncio.run(main(args.apply)))
