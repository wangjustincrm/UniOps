"""The audit trail of what the system said about a record, and to whom.

HSE asked that every message be filed against the record it concerns so the
history is traceable. This is the read side of that. It is deliberately not a
compose window: Safety sends notices, it is not a mail client, and giving it a
"send anything to anyone" box would put untraceable correspondence inside a
statutory record.

Authorisation follows the record. An incident's correspondence is as sensitive
as the incident, so it is gated on the same key rather than on one of its own —
a separate permission is a second thing to get wrong, and drift between the two
opens the record sideways.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.v1.incidents import _may_read, _user_id
from app.core.deps import CurrentUserPayload, SessionDep
from app.crud import incident as incident_crud
from app.models.communication import Communication
from app.services import mail
from app.services import tasks as task_service
from uniops_authz import has_permission

router = APIRouter(prefix="/communications")

# Which permission opens each kind of record's correspondence. A doc_type not
# listed here is refused rather than defaulted — a new record type must make an
# explicit decision about who may read its mail.
READ_KEYS = {
    task_service.DOC_INCIDENT: "ehs.incident.read",
    task_service.DOC_ACTION: "ehs.action.read",
    task_service.DOC_DOCUMENT: "ehs.document.read",
    task_service.DOC_JHSC: "ehs.jhsc.read",
    task_service.DOC_COURSE: "ehs.training.read",
    "ehs_tbt": "ehs.incident.report",
    "ehs_run": "ehs.incident.report",
}


class CommunicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    direction: str
    from_addr: str
    to_addrs: list[str]
    cc_addrs: list[str]
    subject: str | None
    body_text: str | None
    template_key: str | None
    sent_at: datetime | None
    created_at: datetime


@router.get("", response_model=list[CommunicationOut])
async def list_communications(
    db: SessionDep,
    user: CurrentUserPayload,
    doc_type: str = Query(description="Which kind of record"),
    doc_id: uuid.UUID = Query(description="Which record"),
):
    key = READ_KEYS.get(doc_type)
    if key is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"No correspondence is kept for '{doc_type}'")

    uid = _user_id(user)
    if doc_type == task_service.DOC_INCIDENT:
        # An incident's own visibility rule, not a second copy of it: whoever
        # filed a report can see what was sent about it.
        incident = await incident_crud.get(db, doc_id)
        if not await _may_read(db, user, incident):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "You can only see correspondence for incidents you reported or "
                "were involved in")
    elif not await has_permission(db, uid, user.get("role", ""), key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")

    return await mail.history(db, doc_type=doc_type, doc_id=doc_id)


@router.get("/undelivered", response_model=list[CommunicationOut])
async def undelivered(db: SessionDep, user: CurrentUserPayload):
    """Messages that were filed but never left.

    Worth a screen of its own: a run of these means the mail server is
    misconfigured, and the module's failure mode is silence — nothing else
    would tell anybody that a fortnight of escalations went nowhere.
    """
    if not await has_permission(
        db, _user_id(user), user.get("role", ""), "ehs.settings.manage"
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    return list((await db.execute(
        select(Communication)
        .where(Communication.direction == "out", Communication.sent_at.is_(None))
        .order_by(Communication.created_at.desc()).limit(100)
    )).scalars())
