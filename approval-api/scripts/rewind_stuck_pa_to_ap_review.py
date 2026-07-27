"""Rewind in-flight PAs that skipped the AP Review (ap_clerk) step back to it.

The PA approval workflow gained an "AP Review" (role=ap_clerk) step. Documents
already in flight when that step was inserted advanced past its position without
ever stopping there, and a prior config-change resync then left them stranded:
  status = 'in_review', approval_step_idx pointing at/after the last step, and
  NO open approve_pa task — so nobody can act and AP Clerk never reviewed them.

This rewinds each such PA to the AP Review step and creates the broadcast
approve_pa task (assigned_role='ap_clerk', assigned_user_id=NULL) so ANY active
AP Clerk can review it — matching how the live engine emits that step.

Target set (all must hold):
  - status = 'in_review'
  - approval_step_idx > <AP Review index>   (already advanced past AP Review)
  - has NO open approve_pa task              (stranded — not legitimately parked
                                              on a later approver)
Idempotent: a rewound PA sits AT the AP Review index (not > it) with an open
ap_clerk task, so a second run skips it.

Dry-run by DEFAULT (rolls back). Pass --apply to commit. Refuses
ENVIRONMENT=production unless --allow-production.

Run (dev):
    docker exec <approval-container> python -m scripts.rewind_stuck_pa_to_ap_review
    docker exec <approval-container> python -m scripts.rewind_stuck_pa_to_ap_review --apply
(find the container:  docker ps --format '{{.Names}}' | grep approval)
"""
import argparse
import asyncio
import os
import sys

from sqlalchemy import or_, select

from app.crud.workflow import get_workflow
from app.db.base import AsyncSessionLocal
from app.models.event import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.task import Task
from app.models.user import User


async def _ap_review_step(db):
    """(index, node, full_workflow) of the ap_clerk step in the PA workflow."""
    wf = await get_workflow(db, "pa")
    for i, node in enumerate(wf):
        if node.get("role") == "ap_clerk":
            return i, node, wf
    return None, None, wf


async def main(apply: bool, include_imported: bool):
    async with AsyncSessionLocal() as db:
        ap_idx, ap_node, wf = await _ap_review_step(db)
        if ap_idx is None:
            print("No AP Review (ap_clerk) step in the PA workflow — nothing to do.")
            return
        label = ap_node.get("label") or "AP Review"
        n_steps = len(wf)

        # Best-effort actor for the audit event (actor_id is NOT NULL).
        actor_id = (await db.execute(
            select(User.id).where(User.role == "system_admin").limit(1)
        )).scalar_one_or_none()

        conds = [
            PaymentApplication.status == "in_review",
            PaymentApplication.approval_step_idx > ap_idx,
        ]
        if not include_imported:
            # Skip PAs whose approval history is ENTIRELY import-reconstructed
            # (the PMS-migrated historical payments — already settled in the old
            # system; flooding AP Clerk with reviews for them would be wrong).
            # Keep PAs with any live activity (an event that is NULL-comment or
            # not tagged '[reconstructed]').
            has_real_activity = (
                select(ApprovalEvent.id)
                .where(
                    ApprovalEvent.document_type == "pa",
                    ApprovalEvent.document_id == PaymentApplication.id,
                    or_(ApprovalEvent.comment.is_(None),
                        ApprovalEvent.comment.notilike("%reconstructed%")),
                )
                .exists()
            )
            conds.append(has_real_activity)

        pas = (await db.execute(select(PaymentApplication).where(*conds))).scalars().all()

        fixed = 0
        for pa in pas:
            open_approve = (await db.execute(select(Task).where(
                Task.document_type == "pa",
                Task.document_id == pa.id,
                Task.is_completed.is_(False),
                Task.type == "approve_pa",
            ))).scalars().first()
            if open_approve is not None:
                # Legitimately parked on a later approver — leave it alone.
                continue

            prev_idx = pa.approval_step_idx
            pa.approval_step_idx = ap_idx
            db.add(Task(
                type="approve_pa",
                priority="normal",
                document_type="pa",
                document_id=pa.id,
                document_number=pa.pa_number,
                assigned_role="ap_clerk",
                assigned_user_id=None,
                title=f"Approve PA: {pa.pa_number} — {pa.title}",
                description=f"Step {ap_idx + 1}/{n_steps}: {label} review required.",
                amount=pa.payment_amount,
                vendor=pa.vendor_name,
            ))
            if actor_id is not None:
                db.add(ApprovalEvent(
                    document_type="pa",
                    document_id=pa.id,
                    document_number=pa.pa_number,
                    step_idx=ap_idx,
                    action="rewind",
                    actor_id=actor_id,
                    actor_role="system_admin",
                    comment=f"Rewound to AP Review (was step idx {prev_idx}; step had been skipped)",
                ))
            print(f"  PA {pa.pa_number}: idx {prev_idx} -> {ap_idx} (AP Review); created ap_clerk task")
            fixed += 1

        if apply:
            await db.commit()
            print(f"\nAPPLIED: rewound {fixed} PA(s) to AP Review.")
        else:
            await db.rollback()
            print(f"\nDRY-RUN: would rewind {fixed} PA(s) to AP Review. Re-run with --apply to commit.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="commit changes (default: dry-run)")
    ap.add_argument("--include-imported", action="store_true",
                    help="also rewind PAs whose approval history is entirely "
                         "import-reconstructed (default: skip these historical imports)")
    ap.add_argument("--allow-production", action="store_true",
                    help="permit running when ENVIRONMENT=production")
    args = ap.parse_args()

    if os.environ.get("ENVIRONMENT", "").lower() == "production" and not args.allow_production:
        print("Refusing to run against ENVIRONMENT=production without --allow-production.",
              file=sys.stderr)
        sys.exit(2)

    asyncio.run(main(apply=args.apply, include_imported=args.include_imported))
