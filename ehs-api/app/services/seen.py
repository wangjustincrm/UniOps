"""What is new to one person: the counts behind the menu flags.

Round 12, ACT-01/02/03/04 and INC-08 — see app/models/seen.py for why "seen"
is per person and why there is a baseline.

One function computes everything the flags show, and My Actions reads the same
answer for its "New" tile and chips, so the number in the menu, the tile and
the chips on the cards cannot disagree.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feedback import Feedback
from app.models.incident import Incident
from app.models.mirrors import Task
from app.models.news import NewsItem
from app.models.seen import SeenBaseline, SeenMark
from app.services import area_scope, notify
from app.services import tasks as task_service

KIND_TASK = "task"
KIND_INCIDENT = "incident"
KIND_FEEDBACK = "feedback"


async def baseline(db: AsyncSession, user_id: uuid.UUID) -> SeenBaseline:
    """This person's baseline, written the first time it is asked for.

    ★ B5 review B4: when the flags went live — the first baseline anybody was
    given — for everybody who was here then, so nobody's existing work floods
    in as new; for somebody who joined since, when their account was made, so
    the first work handed to a new starter before they ever opened Safety is
    new to them.
    """
    row = await db.get(SeenBaseline, user_id)
    if row is None:
        from sqlalchemy import func

        from app.models.mirrors import User
        now = datetime.now(timezone.utc)
        live = (await db.execute(select(func.min(SeenBaseline.since)))).scalar()
        joined = (await db.execute(select(User.created_at).where(User.id == user_id))).scalar()
        since = joined if live is not None and joined is not None and live < joined < now else now
        await db.execute(insert(SeenBaseline).values(
            user_id=user_id, since=since, news_seen_at=since).on_conflict_do_nothing())
        await db.flush()
        row = await db.get(SeenBaseline, user_id)
    return row


async def mark(db: AsyncSession, user_id: uuid.UUID, kind: str, ids: list[uuid.UUID]) -> None:
    """These have been opened. Opening again moves the time on, which matters
    for feedback: a second reply after the first was read is new again."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return
    await baseline(db, user_id)
    now = datetime.now(timezone.utc)
    stmt = insert(SeenMark).values(
        [{"user_id": user_id, "kind": kind, "item_id": i, "seen_at": now} for i in ids])
    await db.execute(stmt.on_conflict_do_update(
        index_elements=["user_id", "kind", "item_id"], set_={"seen_at": now}))
    await db.flush()


async def _marks(db: AsyncSession, user_id: uuid.UUID, kind: str,
                 ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime]:
    if not ids:
        return {}
    return dict((await db.execute(select(SeenMark.item_id, SeenMark.seen_at).where(
        SeenMark.user_id == user_id, SeenMark.kind == kind,
        SeenMark.item_id.in_(ids)))).all())


@dataclass
class NewIncident:
    id: uuid.UUID
    incident_no: str
    title: str
    form_kind: str
    submitted_at: datetime
    location_path: str | None


@dataclass
class Summary:
    task_ids: list[uuid.UUID] = field(default_factory=list)
    # The corrective actions among those tasks — what the cards are keyed on.
    action_ids: list[uuid.UUID] = field(default_factory=list)
    news: int = 0
    incidents: int = 0
    feedback_answered: int = 0

    @property
    def my_actions(self) -> int:
        """The flag on My Actions: new work, new news and new incidents."""
        return len(self.task_ids) + self.news + self.incidents


# Click-to-sort keys for the new-incident list (the list standard).
_INCIDENT_SORT = {
    "incident_no": "i.incident_no",
    "title": "lower(i.title)",
    "submitted_at": "i.submitted_at",
    "location": "lower(l.path)",
}


async def _incident_scope(db: AsyncSession, user: dict, user_id: uuid.UUID,
                          since: datetime) -> tuple[str, dict] | None:
    """INC-08 / D12: which incidents are new to this person, as SQL.

    Sent since the baseline, near misses included, never one's own report, not
    yet opened by them; every one for HSE, and for whoever is down for an area
    the ones that happened in it.

    ★ B5 review B1: an area owner can be anyone (round 4), and the flag names
    an incident and links to it — so only somebody who may read the incident
    register (ehs.incident.read) is flagged. Others would see titles the
    register does not show them, and a link that answers 403.
    ★ B6: the area is matched in the query, and the list is paged, so neither
    the count nor somebody's own area's older incidents are cut off at a limit.
    """
    from uniops_authz import has_permission

    roles = await notify.roles_held_by(db, user_id, user.get("role", ""))
    site_wide = bool(roles & area_scope.SITE_WIDE_ROLES)
    if not site_wide:
        if not await has_permission(db, user_id, user.get("role", ""), "ehs.incident.read"):
            return None
        areas = await area_scope.areas_for(db, user_id)
        if not areas:
            return None
    where = (" from ehs_incidents i left join locations l on l.id = i.location_id"
             " where i.submitted_at > :since and i.status <> 'draft'"
             "   and (i.reported_by is null or i.reported_by <> :me)"
             "   and not exists (select 1 from ehs_seen s where s.user_id = :me"
             "                   and s.kind = 'incident' and s.item_id = i.id)")
    params: dict = {"since": since, "me": user_id}
    if not site_wide:
        where += ("   and exists (select 1 from unnest(cast(:areas as text[])) a"
                  "               where l.path = a or starts_with(l.path, a || '/'))")
        params["areas"] = list(areas)
    return where, params


async def count_new_incidents(db: AsyncSession, user: dict, user_id: uuid.UUID,
                              since: datetime) -> int:
    scope = await _incident_scope(db, user, user_id, since)
    if scope is None:
        return 0
    where, params = scope
    return (await db.execute(text("select count(*)" + where), params)).scalar_one()  # noqa: S608


async def new_incidents(db: AsyncSession, user: dict, user_id: uuid.UUID, *,
                        sort: str | None, dir: str, offset: int, limit: int
                        ) -> tuple[list[NewIncident], int]:
    from fastapi import HTTPException

    base = await baseline(db, user_id)
    scope = await _incident_scope(db, user, user_id, base.since)
    if scope is None:
        return [], 0
    where, params = scope
    if sort is not None and sort not in _INCIDENT_SORT:
        raise HTTPException(422, f"unknown sort column {sort!r}")
    order = (f"{_INCIDENT_SORT[sort]} {'desc' if dir == 'desc' else 'asc'} nulls last, i.id"
             if sort else "i.submitted_at desc, i.id")
    total = (await db.execute(text("select count(*)" + where), params)).scalar_one()  # noqa: S608
    rows = (await db.execute(text(
        "select i.id, i.incident_no, i.title, i.form_kind, i.submitted_at, l.path"
        + where + f" order by {order} offset :offset limit :limit"),  # noqa: S608 — whitelisted
        {**params, "offset": offset, "limit": limit})).all()
    return [NewIncident(id=r[0], incident_no=r[1], title=r[2], form_kind=r[3],
                        submitted_at=r[4], location_path=r[5]) for r in rows], total


async def summary(db: AsyncSession, user: dict, user_id: uuid.UUID) -> Summary:
    from app.api.v1.actions import assigned_task_rows

    base = await baseline(db, user_id)
    out = Summary()

    tasks = [t for t in await assigned_task_rows(db, user, with_owned_actions=True)
             if t.created_at and t.created_at > base.since]
    seen = await _marks(db, user_id, KIND_TASK, [t.id for t in tasks])
    fresh = [t for t in tasks if t.id not in seen]
    out.task_ids = [t.id for t in fresh]
    out.action_ids = list(dict.fromkeys(
        t.document_id for t in fresh
        if t.document_type == task_service.DOC_ACTION and t.type == task_service.TASK_DO_ACTION))

    out.news = len((await db.execute(select(NewsItem.id).where(
        NewsItem.archived_at.is_(None), NewsItem.posted_at > base.news_seen_at,
        (NewsItem.posted_by.is_(None)) | (NewsItem.posted_by != user_id)))).all())

    out.incidents = await count_new_incidents(db, user, user_id, base.since)

    answered = (await db.execute(select(Feedback.id, Feedback.answered_at).where(
        Feedback.submitted_by == user_id, Feedback.answered_at.is_not(None),
        Feedback.answered_at > base.since))).all()
    marks = await _marks(db, user_id, KIND_FEEDBACK, [r[0] for r in answered])
    out.feedback_answered = sum(1 for fid, at in answered if fid not in marks or marks[fid] < at)
    return out


async def opened_document(db: AsyncSession, user: dict, user_id: uuid.UUID,
                          doc_type: str, doc_id: uuid.UUID) -> None:
    """Somebody opened a record: whatever on it was new to them is not now.

    Their own tasks on it, and the incident itself where it was flagged. A
    task given to a role is marked for this person only — the next supervisor
    still sees it as new.
    """
    from app.api.v1.actions import assigned_task_rows

    rows = await assigned_task_rows(db, user, with_owned_actions=True)
    await mark(db, user_id, KIND_TASK,
               [t.id for t in rows if t.document_type == doc_type and t.document_id == doc_id])
    if doc_type == task_service.DOC_INCIDENT:
        incident = await db.get(Incident, doc_id)
        if incident is not None:
            await mark(db, user_id, KIND_INCIDENT, [incident.id])


async def opened_task(db: AsyncSession, user_id: uuid.UUID, task: Task) -> None:
    await mark(db, user_id, KIND_TASK, [task.id])


async def read_news(db: AsyncSession, user_id: uuid.UUID) -> None:
    row = await baseline(db, user_id)
    row.news_seen_at = datetime.now(timezone.utc)
    await db.flush()


async def read_feedback(db: AsyncSession, user_id: uuid.UUID) -> None:
    ids = list((await db.execute(select(Feedback.id).where(
        Feedback.submitted_by == user_id, Feedback.answered_at.is_not(None)))).scalars())
    await mark(db, user_id, KIND_FEEDBACK, ids)
