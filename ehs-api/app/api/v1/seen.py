"""The new-item flags (round 12, ACT-01/02/03/04, INC-08).

Read by the menu on every page and by My Actions. Writing a mark needs no
permission beyond being signed in: it records only that this person opened
something, and the summary is always about the caller.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, status
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanReportIncident
from app.services import seen

router = APIRouter(prefix="/seen")


class NewIncidentOut(BaseModel):
    id: uuid.UUID
    incident_no: str
    title: str
    form_kind: str
    submitted_at: datetime
    location_path: str | None


class SeenSummaryOut(BaseModel):
    my_actions: int
    task_ids: list[uuid.UUID]
    action_ids: list[uuid.UUID]
    news: int
    # How many incidents are new to me; the list itself is /seen/incidents.
    incidents: int
    feedback_answered: int


@router.get("", response_model=SeenSummaryOut)
async def what_is_new(db: SessionDep, user: CanReportIncident):
    me = _user_id(user)
    s = await seen.summary(db, user, me)
    return SeenSummaryOut(
        my_actions=s.my_actions, task_ids=s.task_ids, action_ids=s.action_ids, news=s.news,
        incidents=s.incidents,
        feedback_answered=s.feedback_answered)


class NewIncidentPage(BaseModel):
    items: list[NewIncidentOut]
    total: int


@router.get("/incidents", response_model=NewIncidentPage)
async def new_incidents(
    db: SessionDep, user: CanReportIncident,
    sort: str | None = None,
    dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
):
    """INC-08: the incidents new to me, paged and sorted on the server."""
    rows, total = await seen.new_incidents(
        db, user, _user_id(user), sort=sort, dir=dir,
        offset=(page - 1) * page_size, limit=page_size)
    return NewIncidentPage(items=[NewIncidentOut(**vars(r)) for r in rows], total=total)


class OpenedIn(BaseModel):
    doc_type: str
    doc_id: uuid.UUID


@router.post("/opened", status_code=status.HTTP_204_NO_CONTENT)
async def opened(payload: OpenedIn, db: SessionDep, user: CanReportIncident):
    """A record's page was opened — from My Actions, an email or anywhere."""
    await seen.opened_document(db, user, _user_id(user), payload.doc_type, payload.doc_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class TaskOpenedIn(BaseModel):
    task_id: uuid.UUID


@router.post("/task", status_code=status.HTTP_204_NO_CONTENT)
async def task_opened(payload: TaskOpenedIn, db: SessionDep, user: CanReportIncident):
    """A card on My Actions was pressed. Needed beside /opened because many
    tasks open a register rather than one record's page."""
    from app.api.v1.actions import assigned_task_rows
    me = _user_id(user)
    mine = {t.id for t in await assigned_task_rows(db, user, with_owned_actions=True)}
    if payload.task_id in mine:
        await seen.mark(db, me, seen.KIND_TASK, [payload.task_id])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/news", status_code=status.HTTP_204_NO_CONTENT)
async def news_read(db: SessionDep, user: CanReportIncident):
    """ACT-01: the news on My Actions has been in front of them."""
    await seen.read_news(db, _user_id(user))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def feedback_read(db: SessionDep, user: CanReportIncident):
    """ACT-04: they have opened Feedback and seen HSE's answers."""
    await seen.read_feedback(db, _user_id(user))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
