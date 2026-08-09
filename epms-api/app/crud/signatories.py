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

from app.crud.current_step import role_label
from app.models.approval import ApprovalEvent
from app.models.user import User

# approval-api records an "approve" event for steps nobody actually acted on
# (the department has no Director, the configured Supervisor is inactive, ...).
# Its actor is the submitter, not an approver, so listing it would print the
# requester's own name in the Approvals table. The sibling marker
# "Auto-approved (same approver holds both roles)" IS a real person and stays.
# Same discriminator the PR/PA detail timelines use client-side.
_AUTO_SKIP_MARKER = "Auto-skipped"


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


async def approval_signatories(
    db: AsyncSession, doc_type: str, doc_id: uuid.UUID, created_by: uuid.UUID | None
) -> tuple[str | None, list[dict]]:
    """Return ``(requester_name, approvals)`` for a PR or PA PDF.

    ``approvals`` is ordered along the workflow chain; each entry is
    ``{"role": <display label>, "name": <full name or None>, "at": <datetime>}``.
    """
    requester = (await resolve_user_names(db, [created_by])).get(created_by)
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
        {"role": role_label(ev.actor_role), "name": name, "at": ev.created_at}
        for ev, name in rows
        if _AUTO_SKIP_MARKER not in (ev.comment or "")
    ]
    return requester, approvals


def _as_uuid(value) -> uuid.UUID | None:
    """Return the UUID a name column is really holding, or None if it's a name."""
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


async def gr_signatories(db: AsyncSession, gr) -> dict:
    """Return the names a GR PDF prints: creator, receiver, acknowledger.

    ``received_by`` / ``acknowledged_by`` are free-text columns the browser fills
    with the user's full name, but non-browser callers (NC import, PMS import,
    Teams) leave them empty and crud/gr.py's fallback stored a bare UUID string.
    Anything that parses as a UUID is looked up and replaced so PDFs for those
    rows print a name instead of an id.
    """
    stored = {"received_by": gr.received_by, "acknowledged_by": gr.acknowledged_by}
    as_uuid = {key: _as_uuid(value) for key, value in stored.items()}
    names = await resolve_user_names(
        db, [gr.created_by, *(u for u in as_uuid.values() if u is not None)]
    )
    resolved = {
        key: (names.get(as_uuid[key]) or value) for key, value in stored.items()
    }
    return {"created_by_name": names.get(gr.created_by), **resolved}
