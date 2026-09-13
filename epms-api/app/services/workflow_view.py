"""Where a document is in its approval chain, and who it is waiting on.

This exists because the assistant was asked "who is it stuck with?" and answered
that the system does not record approvers — which is not true. It records the
chain definition (approval-api), every action taken on it (approval_events), and
who currently holds the open task, including anyone standing in for them. The
data was never the problem; nothing had been wired up to reach it.

Three sources, assembled here rather than in the chat layer so the shape stays
testable on its own:

  * the effective step list for this document, from the approval engine — the
    engine's own answer, including any runtime-injected over-budget steps;
  * the events already recorded against it, which is the history;
  * the open approve task, which is where it is sitting right now.

The current step deliberately comes from `enrich_current_step` rather than from
`approval_step_idx`: over-budget injection and stale routing can skew the index,
and that helper already resolves the assignee's name and any active stand-in.
"""
import uuid
from typing import Any

from sqlalchemy import select

from app.core.access_scope import is_pa_visible, is_po_visible, is_pr_visible
from app.crud import pa as pa_crud
from app.crud import po as po_crud
from app.crud import pr as pr_crud
from app.crud.current_step import enrich_current_step, role_label
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.services import approval_client

SUPPORTED = ("pr", "po", "pa")

_SPEC: dict[str, dict[str, Any]] = {
    "pr": {"model": PurchaseRequest, "number": "number", "crud": pr_crud},
    "po": {"model": PurchaseOrder, "number": "number", "crud": po_crud},
    "pa": {"model": PaymentApplication, "number": "pa_number", "crud": pa_crud},
}


async def resolve(db, scope: dict, doc_type: str, number: str | None,
                  doc_id: str | None):
    """Find the document, honouring visibility.

    Invisible resolves to None — the same as absent. Telling someone which
    approver is sitting on a document they cannot see would leak both that it
    exists and where it is.
    """
    spec = _SPEC.get(doc_type)
    if spec is None:
        return None
    model, number_attr = spec["model"], spec["number"]

    if number:
        doc = (await db.execute(select(model).where(
            getattr(model, number_attr) == number.strip()))).scalar_one_or_none()
    elif doc_id:
        try:
            parsed = uuid.UUID(doc_id)
        except ValueError:
            return None
        doc = (await db.execute(
            select(model).where(model.id == parsed))).scalar_one_or_none()
    else:
        return None
    if doc is None:
        return None

    if doc_type == "pr":
        visible = await is_pr_visible(db, doc.id, scope)
    elif doc_type == "po":
        visible = await is_po_visible(db, doc.id, scope)
    else:
        visible = await is_pa_visible(db, doc, scope)
    return doc if visible else None


async def build(db, doc_type: str, doc, token: str) -> dict:
    """Assemble the chain, the history, and where it is sitting now."""
    spec = _SPEC[doc_type]
    number = getattr(doc, spec["number"])

    # The engine owns the step list; if it cannot be reached, say so rather than
    # implying the document has no chain.
    steps: list[dict] = []
    steps_available = True
    try:
        steps = await approval_client.get_workflow_steps(doc_type, str(doc.id), token)
    except (LookupError, RuntimeError):
        steps_available = False

    events = await spec["crud"].get_approval_events(db, doc.id)
    history = [{
        "step_idx": getattr(e, "step_idx", None),
        "action": getattr(e, "action", None),
        "actor_name": getattr(e, "actor_name", None),
        "actor_role": role_label(getattr(e, "actor_role", "") or ""),
        "comment": getattr(e, "comment", None),
        "at": getattr(e, "created_at", None),
    } for e in events]

    # enrich_current_step only fills documents in review, which is exactly when
    # "who is it with" has an answer.
    await enrich_current_step(db, doc_type, [doc])
    current = getattr(doc, "current_step", None)

    return {
        "doc_type": doc_type,
        "document_number": number,
        "status": getattr(doc, "status", None),
        "steps": steps,
        "steps_available": steps_available,
        "current_step": current,
        "history": history,
    }
