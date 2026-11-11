"""General comments — "this is wrong" about anything at all.

Round 4: "add a general comments feedback place to allow for general comments
for other issues found."

Two audiences, one screen. Anybody may leave a comment and read their own;
whoever manages Settings reads all of them and answers. The narrowing is inside
the functions rather than on the route, for the reason this module has learned
five times over: an ordinary employee holds no settings permission and they are
exactly who has something to say.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanManageSettings, CanReportIncident
from app.crud.incident import _resolve_name
from app.models.feedback import Feedback
from app.services import notify
from uniops_authz import has_permission

router = APIRouter(prefix="/feedback")

STATUSES = ("new", "looked_at", "done")


class FeedbackIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    # Sent by the screen rather than typed. "The list is wrong" cannot be acted
    # on without knowing which list.
    page_path: str | None = Field(default=None, max_length=200)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        """★ A row of spaces is not a comment. `min_length` counts them, and
        the stored value is stripped, so without this a blank row lands in a
        list of complaints looking like somebody had something to say."""
        v = v.strip()
        if not v:
            raise ValueError("Say what you noticed")
        return v


class ReplyIn(BaseModel):
    status: str = Field(pattern="^(new|looked_at|done)$")
    reply: str | None = Field(default=None, max_length=4000)


class FeedbackActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_no: str
    title: str
    status: str
    owner_name: str | None
    due_date: date


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    body: str
    page_path: str | None
    submitted_by: uuid.UUID | None
    submitted_by_name: str | None
    status: str
    reply: str | None
    answered_at: datetime | None
    answered_by_name: str | None
    created_at: datetime
    # ★ Round 12, ACT-04: the corrective actions HSE raised on it, for whoever
    # left it to follow — they cannot browse the action register.
    actions: list[FeedbackActionOut] = []


@router.post("", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED)
async def leave_feedback(payload: FeedbackIn, db: SessionDep, user: CanReportIncident):
    me = _user_id(user)
    row = Feedback(
        id=uuid.uuid4(),
        body=payload.body,
        page_path=payload.page_path,
        submitted_by=me,
        submitted_by_name=await _resolve_name(db, me),
        status="new",
    )
    db.add(row)
    await db.flush()
    # Told, not assigned. A comment is nobody's obligation, and a task nobody
    # can complete never clears — the rule the rest of this module is built on.
    await notify.feedback_left(db, row)
    return FeedbackOut.model_validate(row)


@router.get("", response_model=list[FeedbackOut])
async def list_feedback(
    db: SessionDep,
    user: CanReportIncident,
    mine: bool = Query(
        default=False,
        description="Only your own. Forced on for anyone who cannot manage settings."),
    status_in: list[str] | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, le=500),
):
    """Everybody's, or your own.

    ★ Somebody who cannot manage Settings sees their own comments whatever they
    ask for. Other people's comments about their own work are not a register to
    browse, and answering 403 to a request for the page would hide a person's
    own comments from them.
    """
    me = _user_id(user)
    may_read_all = await has_permission(
        db, me, user.get("role", ""), "ehs.settings.manage")

    stmt = select(Feedback)
    if mine or not may_read_all:
        stmt = stmt.where(Feedback.submitted_by == me)
    if status_in:
        stmt = stmt.where(Feedback.status.in_(status_in))
    rows = list((await db.execute(
        stmt.order_by(Feedback.created_at.desc()).limit(limit))).scalars())
    from app.models.action import Action
    by_comment: dict[uuid.UUID, list] = {}
    if rows:
        for a in (await db.execute(select(Action).where(
                Action.source_type == "feedback",
                Action.source_id.in_([r.id for r in rows])).order_by(Action.created_at))).scalars():
            by_comment.setdefault(a.source_id, []).append(FeedbackActionOut.model_validate(a))
    out = []
    for r in rows:
        item = FeedbackOut.model_validate(r)
        item.actions = by_comment.get(r.id, [])
        out.append(item)
    return out


@router.get("/unread-count")
async def unread_count(db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """How many nobody has looked at. The badge on the way in."""
    n = (await db.execute(
        select(func.count()).select_from(Feedback)
        .where(Feedback.status == "new"))).scalar() or 0
    return {"count": n}


@router.patch("/{feedback_id}", response_model=FeedbackOut)
async def answer_feedback(
    feedback_id: uuid.UUID, payload: ReplyIn, db: SessionDep, user: CanManageSettings,
):
    """★ The comment itself is never edited. Rewording somebody's complaint is
    how a complaint disappears; the reply and the status sit beside it."""
    row = await db.get(Feedback, feedback_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such comment")
    me = _user_id(user)
    row.status = payload.status
    replied = False
    if payload.reply is not None:
        new_reply = payload.reply.strip() or None
        replied = bool(new_reply) and new_reply != row.reply
        row.reply = new_reply
        row.answered_at = datetime.now(timezone.utc)
        row.answered_by = me
        row.answered_by_name = await _resolve_name(db, me)
    await db.flush()
    # ★ Only on a reply that says something new. Otherwise marking a batch as
    # dealt with would post the same mail again to everyone who wrote one.
    if replied:
        await notify.feedback_answered(db, row)
    return FeedbackOut.model_validate(row)
