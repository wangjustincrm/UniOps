"""Resolves every person's name that a generated document PDF prints.

The PDF renderers in app/services/pdf_*.py are synchronous functions handed to
``loop.run_in_executor`` with nothing but an ORM object — they cannot open a DB
session. Names therefore have to be looked up here, in async context, and passed
in as arguments.
"""
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.approval_event_kind import is_machine_approval
from app.crud.current_step import role_label
from app.models.approval import ApprovalEvent
from app.models.user import User

# approval-api and the PMS migration both write action="approve" rows that no
# human actually decided. Their actor is the submitter or the admin service
# account, so listing them would credit the wrong person on the PDF.
#   "Auto-skipped (...)"                          — the step was skipped outright.
#     Also appears prefixed: "[reconstructed] ..." and "Re-synced (config change): ..."
#   "Auto-approved on behalf of <role> — PMS migration (...)" — backfilled at import.
# The sibling "Auto-approved (same approver holds both roles)" IS a real person
# holding two posts and must stay.
# One definition, shared with the events endpoints' `kind` (History tabs and
# timelines): app/core/approval_event_kind.py.


_ON_BEHALF_MARKER = "on behalf of "


def _signatory_name(name: str | None, comment: str | None) -> str | None:
    """"Mohammadi (on behalf of Sivers)" when a stand-in approved.

    The delegator's name is read back out of the event comment the engine
    wrote, so a PDF regenerated years later still shows who the signature was
    really for — even if the delegation row is long gone.
    """
    if not name or not comment or _ON_BEHALF_MARKER not in comment:
        return name
    # rsplit, not split: the engine always APPENDS the delegation suffix at
    # the end of the comment. A user's own free-text comment can legitimately
    # contain the words "on behalf of" earlier (e.g. "Approving on behalf of
    # the whole team — on behalf of Sivers") — split(marker, 1) would take
    # everything after the FIRST occurrence, corrupting the name on a
    # vendor-facing PDF. rsplit always takes the real, engine-appended
    # delegator name at the tail.
    delegator = comment.rsplit(_ON_BEHALF_MARKER, 1)[1].strip()
    return f"{name} ({_ON_BEHALF_MARKER}{delegator})" if delegator else name


async def resolve_user_names(
    db: AsyncSession, ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    """Map user ids to full names in a single query. Unknown/None ids are absent."""
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = await db.execute(
        select(User.id, User.full_name).where(User.id.in_(wanted))
    )
    return {uid: name for uid, name in rows.all()}


# A step token that can be fulfilled by more than one actor role.
_STEP_ROLE_ALIASES = {"gm_or_opm": {"gm", "opm"}}


def _step_matches(step_role: str | None, actor_role: str | None) -> bool:
    if not step_role or not actor_role:
        return False
    return actor_role == step_role or actor_role in _STEP_ROLE_ALIASES.get(step_role, set())


async def _configured_step_labels(doc_type: str, doc_id: uuid.UUID, token: str | None) -> list[dict]:
    """The document's effective workflow steps from the approval engine
    ([{role, label}], over-budget prefix included, so step_idx lines up).
    Empty when no token or the engine cannot be reached — the PDF then falls
    back to the fixed role names; a PDF must never fail over a label."""
    if not token:
        return []
    from app.services import approval_client
    try:
        return await approval_client.get_workflow_steps(doc_type, str(doc_id), token)
    except Exception:  # noqa: BLE001
        return []


async def approval_signatories(
    db: AsyncSession, doc_type: str, doc_id: uuid.UUID, created_by: uuid.UUID | None,
    token: str | None = None,
) -> tuple[str | None, list[dict]]:
    """Return ``(requester_name, approvals)`` for a PR or PA PDF.

    ``approvals`` is ordered along the workflow chain; each entry is
    ``{"role": <display label>, "name": <full name or None>, "at": <datetime>}``.

    The label is the step's name as configured in the approval engine (e.g.
    "Finance Director of Oversea Department", "AP Review") — ISS-0107/0144:
    the workflow is whatever the engine is configured with, so the PDF must not
    print a fixed role name of its own. It is used only when that step's role
    still matches the role the approver acted in (the workflow may have been
    edited since); otherwise, and without a token, the fixed role name is used.
    """
    requester = (await resolve_user_names(db, [created_by])).get(created_by)
    steps = await _configured_step_labels(doc_type, doc_id, token)

    def _label(ev) -> str:
        if 0 <= (ev.step_idx or 0) < len(steps):
            step = steps[ev.step_idx]
            if step.get("label") and _step_matches(step.get("role"), ev.actor_role):
                return step["label"]
        return role_label(ev.actor_role)

    rows = (await db.execute(
        select(ApprovalEvent, User.full_name)
        .outerjoin(User, User.id == ApprovalEvent.actor_id)
        .where(
            ApprovalEvent.document_type == doc_type,
            ApprovalEvent.document_id == doc_id,
            ApprovalEvent.action == "approve",
        )
        .order_by(ApprovalEvent.step_idx, ApprovalEvent.created_at)
    )).all()
    approvals = [
        {
            "role": _label(ev),
            "name": _signatory_name(name, ev.comment),
            "at": ev.created_at,
        }
        for ev, name in rows
        if not is_machine_approval(ev.comment)
    ]
    return requester, approvals


def _as_uuid(value) -> uuid.UUID | None:
    """Return the UUID a name column is really holding, or None if it's a name."""
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


async def gr_signatories(db: AsyncSession, gr) -> dict:
    """Return the names a GR PDF prints: creator, receiver, acknowledger, collector.

    ``received_by`` / ``acknowledged_by`` / ``collected_by`` are free-text columns
    the browser fills with the user's full name, but non-browser callers (NC
    import, PMS import, Teams) leave them empty and crud/gr.py's fallback stored
    a bare UUID string. Anything that parses as a UUID is looked up and replaced
    so PDFs for those rows print a name instead of an id.
    """
    stored = {
        "received_by": gr.received_by,
        "acknowledged_by": gr.acknowledged_by,
        "collected_by": gr.collected_by,
    }
    as_uuid = {key: _as_uuid(value) for key, value in stored.items()}
    names = await resolve_user_names(
        db, [gr.created_by, *(u for u in as_uuid.values() if u is not None)]
    )
    resolved = {
        key: (names.get(as_uuid[key]) or value) for key, value in stored.items()
    }
    return {"created_by_name": names.get(gr.created_by), **resolved}
