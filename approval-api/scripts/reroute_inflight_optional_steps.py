"""Re-route in-flight documents stranded at an optional (Director/Supervisor) step.

Problem
-------
When a department's Director/Supervisor mapping (or a user's role assignment)
changes, documents already parked at that optional step keep the approval task
assigned to the OLD approver. The engine skips unconfigured optional steps for
NEW documents, but in-flight ones stay stuck: the stale assignee can no longer
pass `_actor_can_approve` for that step, so every Approve click 409s.

What this does
--------------
For every document that currently sits on an optional step which — per the
CURRENT config — should now be skipped (Director/Supervisor no longer resolves),
it re-runs the engine's OWN skip/advance logic (the same loop `submit`/`approve`
use): it records skip ApprovalEvents, completes the stale task, advances the
document to the next real step and creates that step's task for the correct
current approver (e.g. the GM), or marks the document approved if no real step
remains. Documents whose optional step is still validly configured are left
untouched (the skip check returns False for them).

Safety
------
- Dry-run by DEFAULT. Pass --apply to commit.
- Refuses to run against ENVIRONMENT=production unless --allow-production.
- Only ever touches documents whose CURRENT step `_should_skip_step` says to
  skip — it never advances a document past a real, still-required approval.

Run (dev):
    docker cp reroute_inflight_optional_steps.py uniops_approval_api:/app/scripts/
    docker exec uniops_approval_api python -m scripts.reroute_inflight_optional_steps
    docker exec uniops_approval_api python -m scripts.reroute_inflight_optional_steps --apply
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

import sqlalchemy as sa

from app.crud import engine as E
from app.crud.workflow import (
    get_dept_director_mapping,
    get_dept_gm_opm_mapping,
    get_dept_supervisor_enabled,
    get_role_management,
)
from app.db.base import AsyncSessionLocal
from app.models.event import ApprovalEvent
from app.models.task import Task
from app.models.user import User


async def _reroute_one(db, doc_type: str, doc_id, *, apply: bool):
    """Return a summary dict if the doc was (or would be) rerouted, else None."""
    meta = E._resolve_meta(doc_type)
    Model = meta["model"]
    doc = (await db.execute(sa.select(Model).where(Model.id == doc_id))).scalar_one_or_none()
    if doc is None:
        return None

    cfg = await E._get_config(db)
    rm = await get_role_management(db)
    dept_gm_opm = await get_dept_gm_opm_mapping(db)
    routing_uid = await E._routing_user_id(db, doc_type, doc)
    dept_director = await get_dept_director_mapping(db)
    dept_supervisor = await get_dept_supervisor_enabled(db)
    director_uid = await E._resolve_director(db, routing_uid, dept_director)
    supervisor_uid = await E._resolve_supervisor(db, routing_uid, dept_supervisor)
    routing_dept = (await db.execute(
        sa.select(User.department_id).where(User.id == routing_uid)
    )).scalar_one_or_none()
    dept_has_director = bool(routing_dept) and str(routing_dept) in (dept_director or {})
    dept_has_supervisor = bool(routing_dept) and bool((dept_supervisor or {}).get(str(routing_dept)))

    workflow = await E.build_effective_workflow(db, doc_type, doc, cfg)
    step = doc.approval_step_idx
    if step >= len(workflow):
        return None

    # Only act when the CURRENT step is one the engine would now skip.
    role = workflow[step]["role"]
    skip_now, _ = E._should_skip_step(
        role, doc_type, doc, director_uid, supervisor_uid, dept_has_director, dept_has_supervisor)
    if not skip_now:
        return None

    number = getattr(doc, meta["number_attr"])
    start = step
    skipped: list[tuple[int, str]] = []
    while start < len(workflow):
        r = workflow[start]["role"]
        sk, reason = E._should_skip_step(
            r, doc_type, doc, director_uid, supervisor_uid, dept_has_director, dept_has_supervisor)
        if not sk:
            break
        if apply:
            db.add(ApprovalEvent(
                document_type=doc_type, document_id=doc.id, document_number=number,
                step_idx=start, action="approve",
                actor_id=routing_uid, actor_role=r,
                comment=f"Re-routed (config change): {reason}",
            ))
        skipped.append((start, r))
        start += 1

    new_role = workflow[start]["role"] if start < len(workflow) else "APPROVED"

    if apply:
        await E._complete_tasks(db, doc_type, doc.id)   # clear the stale optional-step task
        doc.approval_step_idx = start
        if start < len(workflow):
            E._set_status(meta, doc, "in_review")
            await E._create_approve_task(
                db, doc_type, doc, step=start, workflow=workflow, meta=meta,
                rm=rm, dept_gm_opm=dept_gm_opm, routing_uid=routing_uid,
                director_uid=director_uid, supervisor_uid=supervisor_uid)
        else:
            E._set_status(meta, doc, "approved")
            if hasattr(doc, "approved_at"):
                doc.approved_at = datetime.now(timezone.utc)
            post_fn = E._POST_APPROVE.get(doc_type)
            if post_fn is None and doc_type.startswith("cfm_"):
                post_fn = E._post_approve_exp
            if post_fn:
                await post_fn(db, doc)

    return {
        "doc_type": doc_type, "number": number,
        "from_step": step, "to_step": start,
        "skipped": skipped, "new_role": new_role,
    }


async def main(apply: bool):
    async with AsyncSessionLocal() as db:
        # Target set: documents with an OPEN approval task parked on an optional
        # (director / supervisor) step. _reroute_one re-verifies each against the
        # current config, so validly-configured steps are left alone.
        rows = (await db.execute(
            sa.select(Task.document_type, Task.document_id)
            .where(
                Task.type.like("approve%"),
                Task.is_completed.is_(False),
                Task.assigned_role.in_(["director", "supervisor"]),
            )
            .distinct()
        )).all()

        print(f"Candidate documents parked on a director/supervisor step: {len(rows)}")
        done = []
        for doc_type, doc_id in rows:
            try:
                summary = await _reroute_one(db, doc_type, doc_id, apply=apply)
            except Exception as exc:  # keep going; report the offender
                print(f"  ! {doc_type} {doc_id}: ERROR {type(exc).__name__}: {exc}")
                continue
            if summary:
                done.append(summary)
                skip_desc = ", ".join(f"step{si}:{r}" for si, r in summary["skipped"])
                print(f"  {summary['doc_type'].upper()} {summary['number']}: "
                      f"skip [{skip_desc}] -> step{summary['to_step']} ({summary['new_role']})")

        if apply:
            await db.commit()
            print(f"\nAPPLIED: rerouted {len(done)} document(s).")
        else:
            await db.rollback()
            print(f"\nDRY-RUN: would reroute {len(done)} document(s). Re-run with --apply to commit.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="commit changes (default: dry-run)")
    ap.add_argument("--allow-production", action="store_true", help="permit running when ENVIRONMENT=production")
    args = ap.parse_args()

    if os.environ.get("ENVIRONMENT", "").lower() == "production" and not args.allow_production:
        print("Refusing to run against ENVIRONMENT=production without --allow-production.", file=sys.stderr)
        sys.exit(2)

    asyncio.run(main(apply=args.apply))
