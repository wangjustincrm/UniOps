"""Repair imported-PO `created_by` to the real Procurement Officer.

The PO detail's Approval Timeline shows a synthetic "Procurement Officer" step
whose name is `po.created_by`. In the live flow that IS the procurement officer
who converted the approved PR into a PO. But the legacy PMS export carries NO
buyer/creator field for POs (po.json has only Comment/Created/Status/Supplier/
Title/amount), so the importer fell back to the originating PR's creator — a
*requester* / dept_manager — and the timeline shows the wrong person under
"Procurement Officer".

There is no per-PO signal to recover the true buyer, so — exactly like the
reconstructed approvers — we attribute every imported PO's creation to one
designated Procurement Officer (given by --officer-email). Best-effort, not
ground truth; the [reconstructed] approval events already mark these docs.

Scope: imported POs only, detected by carrying at least one [reconstructed]
approval_event (mirrors repair_approval_timeline.py). Idempotent: a PO already
owned by the officer is skipped. Only touches `created_by`; approval routing was
never derived from it (it uses the linked PR's requester), so nothing else moves.

Dry-run by DEFAULT; --commit writes.

Run (dev):
  docker compose ... exec epms-api python -m scripts.import_pms.repair_po_creator --officer-email forough@...
  docker compose ... exec epms-api python -m scripts.import_pms.repair_po_creator --officer-email forough@... --commit
"""
import argparse
import asyncio
import sys

from sqlalchemy import func, select

import app.db.session as sm
from app.models.approval import ApprovalEvent
from app.models.po import PurchaseOrder
from app.models.user import User

_RECON = "[reconstructed]"


async def _repair(db, officer_email: str, commit: bool) -> int:
    officer = (await db.execute(
        select(User).where(func.lower(User.email) == officer_email.strip().lower())
    )).scalar_one_or_none()
    if officer is None:
        print(f"ERROR: no user with email {officer_email!r}")
        return -1
    if not officer.is_active:
        print(f"ERROR: {officer.full_name} ({officer_email}) is INACTIVE — pick an active officer.")
        return -1
    print(f"Attributing imported POs to: {officer.full_name} <{officer.email}> "
          f"(role={officer.role})\n")

    # Imported POs = those carrying at least one reconstructed approval event.
    imported_ids = (await db.execute(
        select(ApprovalEvent.document_id).where(
            ApprovalEvent.document_type == "po",
            ApprovalEvent.comment.like(f"{_RECON}%"),
        ).distinct()
    )).scalars().all()

    pos = (await db.execute(
        select(PurchaseOrder).where(
            PurchaseOrder.id.in_(imported_ids),
            PurchaseOrder.created_by != officer.id,
        ).order_by(PurchaseOrder.number)
    )).scalars().all()

    # Batch-resolve the current owners' names for a readable preview (no N+1).
    old_by_id = dict((await db.execute(
        select(User.id, User.full_name).where(
            User.id.in_({po.created_by for po in pos})
        )
    )).all())

    for i, po in enumerate(pos):
        if i < 20:
            print(f"  {po.number}: {old_by_id.get(po.created_by, po.created_by)} -> {officer.full_name}")
        elif i == 20:
            print(f"  ... and {len(pos) - 20} more")
        if commit:
            po.created_by = officer.id

    if commit:
        await db.commit()
        print(f"\nAPPLIED: re-attributed {len(pos)} imported PO(s) to {officer.full_name}.")
    else:
        await db.rollback()
        print(f"\nDRY-RUN: would re-attribute {len(pos)} imported PO(s). --commit to write.")
    return len(pos)


async def main(officer_email: str, commit: bool) -> None:
    async with sm.AsyncSessionLocal() as db:
        rc = await _repair(db, officer_email, commit)
    if rc < 0:
        sys.exit(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--officer-email", required=True,
                    help="email of the active Procurement Officer to own imported POs")
    ap.add_argument("--commit", action="store_true", help="write (default: dry-run)")
    args = ap.parse_args()
    asyncio.run(main(args.officer_email, args.commit))
