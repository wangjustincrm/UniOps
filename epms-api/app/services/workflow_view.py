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
from datetime import timezone
from typing import Any

from sqlalchemy import select

from app.core import doc_numbers
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


def number_attr(doc_type: str) -> str:
    """The column holding the document's human-facing number."""
    return _SPEC[doc_type]["number"]


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
        # The same reading of a typed number as the query layer's (ISS-0024).
        stored = doc_numbers.normalize(doc_type.upper(), number)
        doc = (await db.execute(select(model).where(
            getattr(model, number_attr) == stored))).scalar_one_or_none()
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


# The PMS migration marks every event it rebuilt with this prefix
# (scripts/import_pms/reconstruct.py RECON_TAG). It is bookkeeping, not
# something anyone wrote: shown as a comment it read as "[reconstructed]" on
# every line of an imported PO's history (ISS-0070).
IMPORTED_MARKER = "[reconstructed]"

# Actions taken AT an approval step, under that step's authority. The engine
# records approve with the step's role, but return and reject with the actor's
# base (JWT) role — so a Finance BP returning a PA was listed as "Requester",
# her primary role (ISS-0070). The write side is the approval engine's; this
# reads the role off the step instead, and changes no stored event.
_STEP_DECISIONS = ("return", "reject")


def _step_role_at(idx, when, approvals: list, steps: list[dict]) -> str | None:
    """The role label of approval step `idx` for this document.

    First choice is what the engine itself recorded for that step on this
    document — an approve event at the same index (nearest earlier one, else
    the first later one): it is the chain as it stood then. Failing that, the
    document's current effective chain from the engine.
    """
    at_step = [(t, role) for (i, t, role) in approvals if i == idx and role]
    if at_step:
        before = [r for t, r in at_step if when is None or t is None or t <= when]
        return role_label(before[-1] if before else at_step[0][1])
    if isinstance(idx, int) and 0 <= idx < len(steps or []):
        step = steps[idx] or {}
        return step.get("label") or (role_label(step["role"]) if step.get("role") else None)
    return None


def history_entries(events, steps: list[dict]) -> list[dict]:
    """The document's events as the assistant shows them (ISS-0070): the PMS
    import marker becomes `imported`, and a return or reject carries the role
    of the step it was decided at."""
    approvals = [(getattr(e, "step_idx", None), getattr(e, "created_at", None),
                  getattr(e, "actor_role", None))
                 for e in events if getattr(e, "action", None) == "approve"]
    out = []
    for e in events:
        action = getattr(e, "action", None)
        comment = getattr(e, "comment", None)
        imported = isinstance(comment, str) and comment.startswith(IMPORTED_MARKER)
        if imported:
            comment = comment[len(IMPORTED_MARKER):].strip() or None
        role = role_label(getattr(e, "actor_role", "") or "")
        if action in _STEP_DECISIONS:
            role = _step_role_at(getattr(e, "step_idx", None),
                                 getattr(e, "created_at", None), approvals, steps) or role
        out.append({
            "step_idx": getattr(e, "step_idx", None),
            "action": action,
            "actor_name": getattr(e, "actor_name", None),
            "actor_role": role,
            "comment": comment,
            "imported": imported,
            "at": getattr(e, "created_at", None),
        })
    return out


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
    history = history_entries(events, steps)

    # enrich_current_step only fills documents awaiting approval (submitted or
    # in review), which is exactly when "who is it with" has an answer.
    await enrich_current_step(db, doc_type, [doc])
    current = getattr(doc, "current_step", None)

    out = {
        "doc_type": doc_type,
        "document_number": number,
        "status": getattr(doc, "status", None),
        "steps": steps,
        "steps_available": steps_available,
        "current_step": current,
        "history": history,
    }
    if doc_type == "pa":
        # Payment is not an approval event, so the history stops at the last
        # approval and "when was it paid" had no answer here (ISS-0043). The
        # payment date is the date Finance entered: paid_at is that date at
        # midnight UTC (see PaymentApplication.paid_on).
        paid_at = getattr(doc, "paid_at", None)
        out["milestones"] = {
            "submitted_at": getattr(doc, "submitted_at", None),
            "approved_at": getattr(doc, "approved_at", None),
            "paid_on": paid_at.astimezone(timezone.utc).date() if paid_at else None,
        }
    return out
