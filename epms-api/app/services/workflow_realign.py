"""After an Approval Workflows save: carry in-flight documents over, finish the
ones it completes, and record the change.

The realignment itself is approval-api's (POST /routing/realign-workflows —
see approval-api app/crud/workflow_realign.py). This side does what only EPMS
can do:

* A document whose every step of the new chain was already given is approved
  inside the realignment (company rule 2026-11-16). The engine runs its own
  post-approval hooks, but EPMS's — the PA/PR/PO PDF, the requester's email, a
  settlement's prepayment, an agreement's payment schedule, the signed PO PDF —
  run in EPMS's action endpoints, after the engine call. They are run here for
  every document the realignment approved, through the same functions.
* Workflow edits had no audit trail: the chain before an edit (what in-flight
  documents were walking) could only be reverse-engineered from approval
  events. Each save that changes a chain writes one admin_audit_log row with
  the chains before and after and what moved.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_audit_log import AdminAuditLog
from app.services import approval_client

logger = logging.getLogger(__name__)


def _changed_keys(before: dict | None, after: dict | None) -> list[str]:
    before, after = before or {}, after or {}
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


async def _finish_approved(db: AsyncSession, item: dict, actor_id: uuid.UUID, token: str) -> None:
    """EPMS's post-approval work for one document the realignment approved."""
    from app.core.background import spawn

    doc_type, number = item["doc_type"], item["number"]
    doc_id = uuid.UUID(item["doc_id"])
    if doc_type == "pa":
        from app.api.v1.pa import on_pa_approved
        from app.crud import pa as pa_crud
        pa = await pa_crud.get_by_id(db, doc_id)
        if pa is not None:
            await on_pa_approved(db, pa, actor_id, token)
    elif doc_type == "pr":
        from app.api.v1.pr import _generate_pr_pdf_background
        spawn(_generate_pr_pdf_background(doc_id, number, token), name=f"pr_pdf:{number}")
    elif doc_type == "po":
        from app.api.v1.po import _generate_po_pdf_background
        spawn(_generate_po_pdf_background(doc_id, number, token), name=f"po_pdf:{number}")
    elif doc_type == "posign":
        from app.api.v1.po import _generate_po_pdf_background
        from app.services.notification import fire_and_forget_signoff_complete
        spawn(_generate_po_pdf_background(doc_id, number, token, replace=True),
              name=f"po_pdf_signed:{number}")
        fire_and_forget_signoff_complete(doc_id)
    elif doc_type == "agr":
        from sqlalchemy import select
        from app.api.v1.agreements import on_agreement_active
        from app.models.agreement import PurchaseAgreement
        agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == doc_id))).scalar_one_or_none()
        if agr is not None and agr.status == "active":
            await on_agreement_active(db, agr)
    # OA claims, VMS visits and budget plans: their post-approval work lives in
    # approval-api's own hooks, which the realignment already ran.


def summarize(result: dict) -> str:
    moved = len(result.get("realigned") or [])
    done = sum(1 for r in result.get("realigned") or [] if r.get("completed"))
    errors = len(result.get("errors") or [])
    text = f"ok: {moved} in-flight document(s) moved to the saved workflow(s)"
    if done:
        text += f", {done} of them fully approved"
    if errors:
        text += f"; {errors} could not be moved — fix them in DM → Edit approval state"
    return text


async def realign_after_save(
    db: AsyncSession, *, before: dict | None, after: dict | None, config_id: uuid.UUID,
    user: dict, token: str,
) -> dict[str, Any]:
    """Run after the workflow_defs save is COMMITTED (approval-api reads the
    shared database on its own connection). Best-effort: the save stands even
    if approval-api is down; the outcome is returned for the admin to see, and
    the next save (or any later call) catches up from the engine's snapshot."""
    actor_id = uuid.UUID(user["sub"])
    try:
        result = await approval_client.realign_workflows(token)
        outcome: dict[str, Any] = {"summary": summarize(result),
                                   "realigned": result.get("realigned") or [],
                                   "errors": result.get("errors") or []}
    except Exception as exc:  # noqa: BLE001 — the save is committed; report, don't raise
        logger.warning("workflow_defs saved but realignment failed: %s", exc)
        outcome = {"summary": f"failed: {exc} — in-flight documents were not moved yet; "
                              "save the workflows again to retry",
                   "realigned": [], "errors": []}

    for item in outcome["realigned"]:
        if item.get("completed"):
            try:
                await _finish_approved(db, item, actor_id, token)
            except Exception:  # noqa: BLE001 — the approval stands; log and go on
                logger.exception("post-approval work failed for %s %s after workflow "
                                 "realignment", item.get("doc_type"), item.get("number"))

    changed = _changed_keys(before, after)
    if changed:
        db.add(AdminAuditLog(
            actor_id=actor_id, actor_email=user.get("email", ""), action="edit_workflow",
            system="epms", entity="workflow_defs", record_id=config_id,
            record_number=", ".join(changed)[:64],
            before={k: (before or {}).get(k) for k in changed},
            after={k: (after or {}).get(k) for k in changed},
            cascade_summary=outcome))
    await db.commit()
    return outcome
