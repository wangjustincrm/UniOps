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
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.v1.incidents import _may_read, _user_id
from app.core.deps import CurrentUserPayload, SessionDep
from app.crud import incident as incident_crud
from app.crud._list_order import sort_clauses
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
    # Round 12, COM-03 / Q16: a reminder says it is one, and a message whose
    # words have been removed says so instead of showing an empty body.
    resend_of: uuid.UUID | None = None
    resent_by_name: str | None = None
    body_purged_at: datetime | None = None
    error: str | None = None


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


# ── Round 12, COM-01 / COM-03 / Q16: every email, for HSE ────────────────────

class LogRow(BaseModel):
    """One email, without its words. The words carry injury details and what
    somebody wrote in feedback; the list says who, what about and when, and
    the words are opened one at a time."""

    id: uuid.UUID
    created_at: datetime
    sent_at: datetime | None
    subject: str | None
    template_key: str | None
    doc_type: str | None
    doc_id: uuid.UUID | None
    doc_number: str | None
    to_addrs: list[str]
    to_names: list[str]
    error: str | None
    resend_of: uuid.UUID | None
    resent_by_name: str | None
    body_purged_at: datetime | None
    # Where the record it is about opens, or "" when nowhere better.
    path: str


class LogPage(BaseModel):
    items: list[LogRow]
    total: int
    retention_days: int


_LOG_SORTABLE = {
    "created_at": Communication.created_at,
    "subject": Communication.subject,
    "doc_number": Communication.doc_number,
    "sent_at": Communication.sent_at,
}


async def _may_see_the_log(db, user) -> None:
    """Q16: HSE only — the people holding Settings, which is ehs_manager."""
    if not await has_permission(
        db, _user_id(user), user.get("role", ""), "ehs.settings.manage"
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")


def _path(doc_type: str | None, doc_id) -> str:
    template = task_service.DOC_PATHS.get(doc_type or "")
    if not template or doc_id is None or "{ref}" in template:
        return ""
    return template.format(id=doc_id)


@router.get("/log", response_model=LogPage)
async def communications_log(
    db: SessionDep,
    user: CurrentUserPayload,
    q: str | None = Query(default=None, max_length=100,
                          description="A person's name or address, a record number, or words in the subject"),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    state: str = Query(default="all", pattern="^(all|sent|unsent|reminders)$"),
    sort: str | None = None,
    dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    """★ COM-01: every email Safety sent, not only the ones that never left —
    they were always all filed, and the page showed one kind. COM-03: searched
    on the person, the record's number and the subject, between two dates."""
    from sqlalchemy import String, cast, func, or_, text

    from app.models.config import EhsConfig
    from app.models.mirrors import User
    from app.services.day_range import day_bounds

    await _may_see_the_log(db, user)
    where = [Communication.direction == "out"]
    if q and q.strip():
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        # The people it went to, as they were matched when it was filed.
        named = text(
            "exists (select 1 from users u where u.full_name ilike :like escape '\\'"
            " and jsonb_exists(ehs_communications.to_user_ids, u.id::text))").bindparams(like=like)
        where.append(or_(
            named,
            cast(Communication.to_addrs, String).ilike(like, escape="\\"),
            Communication.doc_number.ilike(like, escape="\\"),
            Communication.subject.ilike(like, escape="\\"),
        ))
    start, end = day_bounds(date_from, date_to)
    if start is not None:
        where.append(Communication.created_at >= start)
    if end is not None:
        where.append(Communication.created_at < end)
    if state == "sent":
        where.append(Communication.sent_at.is_not(None))
    elif state == "unsent":
        where.append(Communication.sent_at.is_(None))
    elif state == "reminders":
        where.append(Communication.resend_of.is_not(None))

    total = (await db.execute(select(func.count()).select_from(Communication).where(*where))).scalar_one()
    order = sort_clauses(sort, dir, _LOG_SORTABLE, [Communication.id]) or [
        Communication.created_at.desc(), Communication.id]
    rows = list((await db.execute(
        select(Communication).where(*where).order_by(*order)
        .offset((page - 1) * page_size).limit(page_size))).scalars())

    ids = {i for r in rows for i in (r.to_user_ids or [])}
    names = dict((str(k), v) for k, v in (await db.execute(
        select(User.id, User.full_name).where(cast(User.id, String).in_(list(ids))))).all()) if ids else {}
    cfg = await db.get(EhsConfig, 1)
    return LogPage(total=total, retention_days=cfg.communication_retention_days if cfg else 21, items=[
        LogRow(id=r.id, created_at=r.created_at, sent_at=r.sent_at, subject=r.subject,
               template_key=r.template_key, doc_type=r.doc_type, doc_id=r.doc_id,
               doc_number=r.doc_number, to_addrs=r.to_addrs or [],
               to_names=[names[i] for i in (r.to_user_ids or []) if i in names],
               error=r.error, resend_of=r.resend_of, resent_by_name=r.resent_by_name,
               body_purged_at=r.body_purged_at, path=_path(r.doc_type, r.doc_id))
        for r in rows])


@router.get("/log/{communication_id}", response_model=CommunicationOut)
async def one_message(communication_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    """The words of one message, opened from the log by HSE."""
    await _may_see_the_log(db, user)
    row = await db.get(Communication, communication_id)
    if row is None or row.direction != "out":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such outgoing message")
    return row


class RemindIn(BaseModel):
    to: list[str]


@router.post("/{communication_id}/remind", response_model=CommunicationOut,
             status_code=status.HTTP_201_CREATED)
async def remind(communication_id: uuid.UUID, payload: RemindIn, db: SessionDep,
                 user: CurrentUserPayload):
    """★ COM-03: "a button to resend the communication as a reminder to the
    worker. log this resending on the appropriate form". Q16: to the people
    HSE picks, from those it went to — not a way to send it to anybody else.
    """
    from app.models.mirrors import User

    await _may_see_the_log(db, user)
    row = await db.get(Communication, communication_id)
    if row is None or row.direction != "out":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such outgoing message")
    if row.body_purged_at is not None or not row.body_text:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Its words have been removed after the retention period, "
                            "so it cannot be sent again")
    went_to = {a.lower(): a for a in [*(row.to_addrs or []), *(row.cc_addrs or [])]}
    picked = [went_to[a.strip().lower()] for a in payload.to if a.strip().lower() in went_to]
    strangers = [a for a in payload.to if a.strip().lower() not in went_to]
    if strangers:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"It did not go to {', '.join(strangers)}; a reminder goes to "
                            "people it was sent to")
    if not picked:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick who to remind")
    me = await db.get(User, _user_id(user))
    return await mail.remind(db, row, to=list(dict.fromkeys(picked)),
                             by_name=me.full_name if me else None)


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


class SendAgainOut(BaseModel):
    sent: bool
    message: str


@router.post("/{communication_id}/send-again", response_model=SendAgainOut)
async def send_again(communication_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    """Try once more to send a message that was filed but never left.

    ★ Review (uniops-8b): nothing ever retried one — a process stopped between
    saving and sending, a mail server that refused, or no mail server set up.
    By hand rather than on a timer, on purpose: once the mail server is fixed,
    a timer would send a fortnight of stale notices at once; HSE chooses which
    are still worth sending. Sent at most once (`deliver` checks `sent_at`).
    """
    if not await has_permission(
        db, _user_id(user), user.get("role", ""), "ehs.settings.manage"
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    row = await db.get(Communication, communication_id)
    if row is None or row.direction != "out":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such outgoing message")
    if row.sent_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This message has already been sent")
    if row.body_purged_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Its words have been removed after the retention period, "
                            "so it cannot be sent")
    if await mail.deliver(row.id):
        return SendAgainOut(sent=True, message="Sent.")
    return SendAgainOut(sent=False, message=(
        "Still not sent. Check the mail server under Admin → Company Settings; "
        "the server log has the reason."))
