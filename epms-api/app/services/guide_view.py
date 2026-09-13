"""The guidance half: what is waiting for this person, and how a process works.

This is the need that came first — a lot of people were never trained on the
system, so the assistant is where they learn it — and it was the last thing
built. Two questions it answers:

  "What should I be doing?"  → their own open tasks
  "How does purchasing work?" → the chain as the engine actually defines it

The second deliberately reads the approval engine's configuration rather than
any document. The PRDs describing these flows are two to four months behind
production; a workflow generated from the live config cannot drift, because it
IS what runs.
"""
import uuid

from app.crud import task as task_crud
from app.crud.current_step import role_label
from app.services import approval_client

# doc_type -> how to say it to a person
PROCESS_LABELS = {
    "pr": "Purchase Request",
    "po": "Purchase Order",
    "pa": "Payment Application",
    "vms_visit": "Visitor Request",
}
SUPPORTED_PROCESSES = tuple(PROCESS_LABELS)


async def my_tasks(role: str, user_id: uuid.UUID) -> list[dict]:
    """This person's open tasks, exactly as their inbox computes them.

    Calls the inbox's own get_for_role rather than re-deriving "assigned to me":
    a task reaches someone through a personal assignment, through any role they
    hold, or through a delegation, and a second implementation of that would
    start disagreeing with the inbox about what they owe.

    It runs on its own session for two reasons. The controlled-query path sets
    the transaction READ ONLY, and get_for_role performs the inbox's self-healing
    backfills — which are writes. Sharing a session would either break those or
    break the query layer's guarantee.
    """
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        tasks = await task_crud.get_for_role(db, role=role, user_id=user_id,
                                             is_completed=None)
        return [{
            "type": t.type,
            "document_type": t.document_type,
            "document_number": t.document_number,
            "title": t.title,
            "description": t.description,
            "priority": t.priority,
            "due_date": t.due_date,
            "amount": str(t.amount) if t.amount is not None else None,
            "vendor": t.vendor,
            # A task can reach someone through a role rather than by name; saying
            # which makes "why is this mine?" answerable.
            "assigned_to_me_personally": t.assigned_user_id == user_id,
            "assigned_role": role_label(t.assigned_role) if t.assigned_role else None,
            "created_at": t.created_at,
        } for t in tasks]


async def process_steps(doc_type: str, token: str) -> dict:
    """The configured approval chain for a kind of document.

    Read from the engine, never from documentation. Steps marked optional are
    skipped per-document at runtime (a department with no director, an
    under-budget PR), so the list is the shape of the process rather than a
    promise about any one document.
    """
    steps: list[dict] = []
    available = True
    try:
        status, body = await approval_client.forward(
            "GET", f"/workflows/{doc_type}", token)
        if status >= 400:
            available = False
        else:
            steps = (body or {}).get("steps", []) if isinstance(body, dict) else []
    except Exception:  # noqa: BLE001
        # forward() lets httpx errors through rather than wrapping them, so a
        # narrower except let a ConnectError escape and turn "the engine is
        # down" into a 500. Any failure here means the same thing: the chain
        # could not be read, and saying so beats describing one from memory.
        available = False

    return {
        "doc_type": doc_type,
        "label": PROCESS_LABELS.get(doc_type, doc_type.upper()),
        "steps": [{
            "index": i + 1,
            "role": s.get("role"),
            "label": s.get("label") or role_label(s.get("role") or ""),
            # The engine annotates these; the stored definition carries no such
            # flag, so reading one off it reported every step as mandatory and
            # the assistant told a requester a PR always needs a Director.
            "conditional": bool(s.get("conditional")),
            "condition": s.get("condition"),
        } for i, s in enumerate(steps)],
        "steps_available": available,
    }
