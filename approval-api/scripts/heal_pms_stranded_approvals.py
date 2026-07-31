"""Heal PMS-imported in-flight PA/PO stranded 'in_review' with NO open approve task.

Root cause (verified against the PMS SharePoint 'Payment Request' / 'PO List'):
the PMS->EPMS import reconstructed each in-flight doc's approval HISTORY (tagged
'[reconstructed]') and even created completed tasks for already-approved steps,
but never materialized the OPEN approve task for the doc's CURRENT pending step.
`resync_inflight_approvals` can't rescue them because its worklist is gated on
having an open approve task (engine.py) — a no-task doc is invisible to it.

The disposition per doc is driven by the doc's AUTHORITATIVE PMS status, captured
in scripts/data/heal_map.json (Title=doc number -> {role, step, mode, pms_status}):

  mode='task'    -> the doc is genuinely mid-approval at `role` (step `step`);
                    create the approve task there via the live engine
                    (_resync_document) so broadcast/pinned semantics match reality.
  mode='approve' -> PMS status was 'FN MANAGER APPROVING' (finance_manager is the
                    TERMINAL PA step). Per operator decision, do NOT make the FM
                    re-approve these historical payments: auto-approve on behalf of
                    the Finance Manager -> status 'approved' + approved_at + an audit
                    ApprovalEvent + the normal post-approve side effect (a process_pa
                    payment task for AP Clerk).

Idempotent: a doc that already has an open approve task, or is no longer
'in_review', is skipped. Each doc runs inside its own SAVEPOINT so one failure
(e.g. an unresolvable Department Manager) can't corrupt the batch.

Dry-run by DEFAULT (rolls back). Pass --apply to commit. Refuses
ENVIRONMENT=production unless --allow-production.

Run (on the app server, approval-api container image):
    docker compose -f docker-compose.prod.yml run --rm \
      -v /opt/uniops/approval-api/scripts:/app/scripts approval-api \
      python -m scripts.heal_pms_stranded_approvals --allow-production          # dry-run
    ... python -m scripts.heal_pms_stranded_approvals --apply --allow-production  # commit
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.crud import engine as E
from app.db.base import AsyncSessionLocal
from app.models.event import ApprovalEvent
from app.models.task import Task
from app.models.user import User

MAP_PATH = Path(__file__).parent / "data" / "heal_map.json"


async def _has_open_approve(db, doc_type: str, doc_id) -> bool:
    r = await db.execute(
        select(Task.id).where(
            Task.document_type == doc_type,
            Task.document_id == doc_id,
            Task.type == f"approve_{doc_type}",
            Task.is_completed.is_(False),
        ).limit(1)
    )
    return r.scalar_one_or_none() is not None


async def _load(db, doc_type: str, number: str):
    meta = E._resolve_meta(doc_type)
    Model = meta["model"]
    r = await db.execute(select(Model).where(getattr(Model, meta["number_attr"]) == number))
    return meta, r.scalar_one_or_none()


async def _heal_one(db, doc_type: str, number: str, spec: dict, actor_id, now) -> str:
    """Return a one-word outcome. Runs inside the caller's SAVEPOINT."""
    meta, doc = await _load(db, doc_type, number)
    if doc is None:
        return "missing"
    if E._status_of(meta, doc) != "in_review":
        return "not_in_review"
    if await _has_open_approve(db, doc_type, doc.id):
        return "already_has_task"

    step, role, mode = spec["step"], spec["role"], spec["mode"]

    if mode == "approve":
        # Auto-approve the terminal finance_manager step on behalf of the FM.
        doc.approval_step_idx = step
        db.add(ApprovalEvent(
            document_type=doc_type, document_id=doc.id,
            document_number=getattr(doc, meta["number_attr"]), step_idx=step,
            action="approve", actor_id=actor_id, actor_role=role,
            comment=(f"Auto-approved on behalf of Finance Manager — PMS migration "
                     f"(PMS status '{spec.get('pms_status')}'/OPEN; FM sign-off carried over)"),
        ))
        E._set_status(meta, doc, "approved")
        if hasattr(doc, "approved_at"):
            doc.approved_at = now
        post_fn = E._POST_APPROVE.get(doc_type)
        if post_fn:
            await post_fn(db, doc)
        await db.flush()
        return "approved"

    # mode == 'task': re-materialize the approve task at the PMS-indicated step,
    # via the live engine so dept_manager pins / gm_or_opm broadcast match reality.
    doc.approval_step_idx = step
    await E._resync_document(db, doc_type, doc.id)
    await db.flush()
    return "task_created" if await _has_open_approve(db, doc_type, doc.id) else "no_task"


async def main(apply: bool, only: str | None):
    data = json.loads(MAP_PATH.read_text())
    counts: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        actor_id = (await db.execute(
            select(User.id).where(User.role == "system_admin").limit(1))).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        for doc_type in ("pa", "po"):
            if only and only != doc_type:
                continue
            for number, spec in data.get(doc_type, {}).items():
                try:
                    async with db.begin_nested():
                        outcome = await _heal_one(db, doc_type, number, spec, actor_id, now)
                except Exception as exc:  # keep going; report the offender
                    outcome = f"error:{type(exc).__name__}"
                    print(f"  ! {doc_type.upper()} {number}: {type(exc).__name__}: {exc}")
                else:
                    if outcome in ("approved", "task_created", "no_task"):
                        print(f"  {outcome:>12}  {doc_type.upper()} {number} -> {spec['role']}@step{spec['step']}")
                counts[outcome] = counts.get(outcome, 0) + 1

        print("\n=== SUMMARY ===")
        for k in sorted(counts):
            print(f"  {counts[k]:>4}  {k}")
        skips = data.get("skip", [])
        if skips:
            print(f"\n  (heal_map.json also lists {len(skips)} doc(s) NOT healed — handled separately:)")
            for s in skips:
                print(f"      - {s['type'].upper()} {s['number']}: {s['reason']} (PMS {s.get('pms_status')}/{s.get('status0')})")

        if apply:
            await db.commit()
            print("\nAPPLIED (committed).")
        else:
            await db.rollback()
            print("\nDRY-RUN (rolled back). Re-run with --apply to commit.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="commit changes (default: dry-run)")
    ap.add_argument("--only", choices=["pa", "po"], help="restrict to one doc type")
    ap.add_argument("--allow-production", action="store_true",
                    help="permit running when ENVIRONMENT=production")
    args = ap.parse_args()

    if os.environ.get("ENVIRONMENT", "").lower() == "production" and not args.allow_production:
        print("Refusing to run against ENVIRONMENT=production without --allow-production.",
              file=sys.stderr)
        sys.exit(2)

    asyncio.run(main(apply=args.apply, only=args.only))
