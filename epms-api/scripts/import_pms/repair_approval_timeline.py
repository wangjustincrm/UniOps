"""Repair imported-doc Approval Timeline after the workflow gained steps.

PMS-imported PR/PO/PA got their approval_step_idx from a hardcoded legacy order,
and their reconstructed approval_events were indexed against whatever workflow
existed at import time. Later the UniOps workflow gained a **Director** step (and
now **AP Review / ap_clerk**), shifting every later role's index — so the
Approval Progress renders against the wrong roles and Director shows as pending
instead of skipped.

This realigns each imported doc to the CURRENT workflow WITHOUT re-importing:
  • re-index each [reconstructed] approve/reject event by its actor_role's current
    position (role-based → immune to whatever workflow existed at import time);
  • drop any stale Director/Supervisor *approve* event and insert an Auto-skipped
    one instead (legacy PMS had no such role);
  • translate approval_step_idx (legacy assumed order → role → current index);
  • complete the stale open approve task so a follow-up `resync-inflight`
    recreates the correct one for the real current approver.

Idempotent-ish (re-index by role is stable; skip inserts are de-duped). Dry-run
by DEFAULT; --apply commits. Only touches docs that have [reconstructed] events.

Run (dev):
  docker compose ... exec epms-api python -m scripts.import_pms.repair_approval_timeline
  docker compose ... exec epms-api python -m scripts.import_pms.repair_approval_timeline --apply
Then run the approval resync to rebuild tasks:
  docker compose ... exec approval-api python -m scripts.reroute_inflight_optional_steps --apply
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import select

import app.db.session as sm
from app.models.approval import ApprovalEvent
from app.models.config import CompanyConfig
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from . import mappings as M
from .reconstruct import _FULLY_APPROVED

_MODELS = {"pr": PurchaseRequest, "po": PurchaseOrder, "pa": PaymentApplication}
_SKIP_ROLES = {"director", "supervisor"}
_RECON = "[reconstructed]"


async def _repair(db, apply: bool) -> list[str]:
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
    wf_defs = (cfg.workflow_defs if cfg else {}) or {}
    out: list[str] = []
    now = datetime.now(timezone.utc)

    for doc_type, Model in _MODELS.items():
        roles = [n["role"] for n in (wf_defs.get(doc_type) or [])]
        if not roles:
            continue
        # imported docs = those carrying at least one reconstructed event
        ids = (await db.execute(
            select(ApprovalEvent.document_id).where(
                ApprovalEvent.document_type == doc_type,
                ApprovalEvent.comment.like(f"{_RECON}%"),
            ).distinct()
        )).scalars().all()

        for doc_id in ids:
            doc = (await db.execute(select(Model).where(Model.id == doc_id))).scalar_one_or_none()
            if doc is None:
                continue
            evs = (await db.execute(select(ApprovalEvent).where(
                ApprovalEvent.document_id == doc_id,
                ApprovalEvent.comment.like(f"{_RECON}%"),
            ))).scalars().all()

            actions: list[str] = []
            # Where the doc really sits now: fully-approved statuses = all steps done
            # (their stored step is unreliable — e.g. approved PRs sit at step 0);
            # mid-approval = translate the stored step; terminal/other = step 0.
            status = doc.status
            if status in _FULLY_APPROVED.get(doc_type, set()):
                new_step = len(roles)
            elif status in ("in_review", "submitted"):
                new_step = M.translate_step_idx(doc_type, doc.approval_step_idx or 0, roles)
            else:
                new_step = 0

            # 1) re-index / clean reconstructed events by role
            present_steps: set[int] = set()
            for e in evs:
                if e.action == "submit":
                    present_steps.add(0)
                    continue
                if e.actor_role in _SKIP_ROLES:
                    if apply:
                        await db.delete(e)          # stale fake approve on an optional step
                    actions.append(f"drop {e.actor_role}@{e.step_idx}")
                    continue
                if e.actor_role in roles:
                    tgt = roles.index(e.actor_role)
                    if e.step_idx != tgt:
                        if apply:
                            e.step_idx = tgt
                        actions.append(f"{e.actor_role} {e.step_idx}->{tgt}")
                    present_steps.add(tgt)

            # 2) insert Auto-skipped events for optional steps now in the DONE range
            for i in range(min(new_step, len(roles))):
                if roles[i] in _SKIP_ROLES and i not in present_steps:
                    if apply:
                        db.add(ApprovalEvent(
                            document_type=doc_type, document_id=doc.id,
                            document_number=getattr(doc, "number", None) or getattr(doc, "pa_number", ""),
                            step_idx=i, action="approve", actor_id=doc.created_by,
                            actor_role=roles[i],
                            comment=f"{_RECON} Auto-skipped ({roles[i]} not in legacy system)",
                            created_at=doc.created_at,
                        ))
                    actions.append(f"skip {roles[i]}@{i}")

            # 3) translate the stored step pointer
            if (doc.approval_step_idx or 0) != new_step:
                if apply:
                    doc.approval_step_idx = new_step
                actions.append(f"step {doc.approval_step_idx}->{new_step}")

            # 4) complete stale open approve task (resync will recreate the right one)
            open_tasks = (await db.execute(select(Task).where(
                Task.document_type == doc_type, Task.document_id == doc.id,
                Task.type == f"approve_{doc_type}", Task.is_completed.is_(False),
            ))).scalars().all()
            if open_tasks:
                if apply:
                    for t in open_tasks:
                        t.is_completed = True
                        t.completed_at = now
                actions.append(f"complete {len(open_tasks)} task")

            if actions:
                out.append(f"{doc_type.upper()} {getattr(doc,'number',None) or getattr(doc,'pa_number','')}: "
                           + "; ".join(actions))
    return out


async def main(apply: bool):
    async with sm.AsyncSessionLocal() as db:
        lines = await _repair(db, apply)
        for ln in lines:
            print("  " + ln)
        if apply:
            await db.commit()
            print(f"\nAPPLIED: repaired {len(lines)} document(s). "
                  f"Now run the approval resync to rebuild tasks.")
        else:
            await db.rollback()
            print(f"\nDRY-RUN: would repair {len(lines)} document(s). --apply to commit.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(apply=args.apply))
