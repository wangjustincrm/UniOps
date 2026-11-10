"""Corrective and preventive action endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, false, not_, or_, select

from app.core.deps import SessionDep
from uniops_authz import has_permission

from app.services import monthly as monthly_service
from app.core.permissions import (
    CanReadAction, CanReportIncident, CanWriteAction,
)
from app.crud import action as crud
from app.models.mirrors import Task
from app.models.action import Action
from app.services import tasks as task_service
from app.services import area_scope
from app.services import notify
from app.schemas.action import (
    ActionCreate,
    ActionDetail,
    ActionOut,
    ActionUpdateIn,
    ActionUpdateOut,
    ActionVerificationOut,
    ActionVerifyIn,
)

router = APIRouter()


async def _may_browse(db, payload: dict) -> bool:
    """Whether this caller sees the whole action register or only their own.

    ehs.incident.read is the marker for "can browse the register" — HSE, area
    supervisors, JHSC members and auditors hold it; production workers do not.
    A worker holds ehs.action.read so they can open and update the actions
    assigned to them, which is not the same as reading everybody's.
    """
    return await has_permission(
        db, _user_id(payload), payload.get("role", ""), "ehs.incident.read")


def _user_id(payload: dict) -> uuid.UUID:
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token carries no usable subject") from None


async def _detail(db, action) -> ActionDetail:
    detail = ActionDetail.model_validate(action)
    detail.updates = [ActionUpdateOut.model_validate(u) for u in await crud.load_updates(db, action.id)]
    detail.verifications = [
        ActionVerificationOut.model_validate(v) for v in await crud.load_verifications(db, action.id)
    ]
    return detail


async def _refuse_on_a_closed_incident(db, payload: ActionCreate) -> None:
    """★ Round-6 review: closing now waits for its corrective actions, and a
    new one could still be raised on the closed record afterwards — an open
    action on a closed incident, the state closing exists to rule out. The
    incident is found by its id (named directly, or through the cause), not by
    the source_type the caller sent.
    """
    from app.crud import incident as incident_crud
    from app.models.incident import Cause, Incident

    incident_id = payload.source_id
    if payload.cause_id is not None:
        cause = await db.get(Cause, payload.cause_id)
        if cause is not None and cause.incident_id is not None:
            incident_id = cause.incident_id
    incident = await db.get(Incident, incident_id) if incident_id else None
    if incident is not None:
        incident_crud.assert_open(incident, "its corrective actions")


@router.post("", response_model=ActionDetail, status_code=status.HTTP_201_CREATED)
async def create_action(payload: ActionCreate, db: SessionDep, user: CanWriteAction):  # noqa: ARG001
    await _refuse_on_a_closed_incident(db, payload)
    action = await crud.create(db, payload)
    return await _detail(db, action)


@router.get("", response_model=list[ActionOut])
async def list_actions(
    db: SessionDep,
    user: CanReadAction,
    owner_id: uuid.UUID | None = None,
    status_in: list[str] | None = Query(default=None, alias="status"),
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    cause_id: uuid.UUID | None = None,
    overdue: bool = False,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
):
    # Without the browse marker the list narrows to your own rather than
    # refusing — the same shape as the incident register.
    if not await _may_browse(db, user):
        owner_id = _user_id(user)
    rows = await crud.list_(
        db, owner_id=owner_id, statuses=status_in, source_type=source_type,
        source_id=source_id, cause_id=cause_id, overdue_only=overdue,
        limit=limit, offset=offset,
    )
    return [ActionOut.model_validate(r) for r in rows]


class MonthItemOut(BaseModel):
    key: str
    label: str
    done: bool
    detail: str
    path: str
    count: int = 0


class MonthOut(BaseModel):
    month: str
    label: str
    is_current: bool
    items: list[MonthItemOut]


@router.get("/my-month", response_model=list[MonthOut])
async def my_month(db: SessionDep, user: CanReportIncident):
    """What a supervisor owes this month and next: a toolbox talk, a hazard
    assessment and a near miss.

    Round 5, answer box 11 and round 4, TC-08. Derived from the records rather
    than raised as tasks — an unmet month is "simply missed", so there is nothing
    to chase or close. Empty for anybody who does not run a crew.

    ★ Only ever the caller's own. See `services/monthly.py` on anonymous near
    misses before building anything that shows this for somebody else.
    """
    months = await monthly_service.for_user(db, _user_id(user), user.get("role", ""))
    return [MonthOut(month=m.month, label=m.label, is_current=m.is_current,
                     items=[MonthItemOut(**i.__dict__) for i in m.items])
            for m in months]


@router.get("/mine", response_model=list[ActionOut])
async def my_actions(
    db: SessionDep,
    user: CanReadAction,
    include_closed: bool = False,
):
    """What is assigned to the caller — the landing page for most employees."""
    # What is mine to do, which is not the same as what I own. An action handed
    # over for verification is somebody else's move, and it used to sit in this
    # list afterwards looking like outstanding work — the second acceptance
    # round reported it as items not leaving the screen once completed. They
    # come back if a verification finds the control ineffective, which is the
    # point of verifying.
    statuses = None if include_closed else ["open", "in_progress"]
    rows = await crud.list_(db, owner_id=_user_id(user), statuses=statuses, limit=200)
    return [ActionOut.model_validate(r) for r in rows]


@router.get("/mine/to-verify", response_model=list[ActionOut])
async def actions_to_verify(db: SessionDep, user: CanReadAction):
    """Waiting on this caller to confirm the control works.

    Separate from "my actions" because it is a different job: one is work to
    do, the other is judging whether somebody else's worked. Holding
    `ehs.action.verify` shows all of them; without it you see the ones you own,
    which HSE asked for.
    """
    me = _user_id(user)
    may_verify_any = await has_permission(
        db, me, user.get("role", ""), "ehs.action.verify")
    # Round 12, Q7: only supervisors and HSE verify, so nobody else has any.
    if not may_verify_any:
        return []
    rows = await crud.list_(db, statuses=["pending_verification"], limit=200)
    return [ActionOut.model_validate(r) for r in rows]


class AssignedTaskOut(BaseModel):
    """One thing assigned to this person that is not a corrective action."""

    id: uuid.UUID
    type: str
    title: str
    description: str | None
    priority: str
    document_type: str
    document_id: uuid.UUID
    document_number: str
    created_at: datetime
    # Where to go to deal with it; empty when this module has no screen for it.
    path: str


class UnattributedOut(BaseModel):
    """Work that belongs to a part of the plant nobody is down for."""

    count: int
    examples: list[str]


@router.get("/unattributed", response_model=UnattributedOut)
async def unattributed(db: SessionDep, user: CanReadAction):
    """How much role work has no area owner.

    ★ Its own question, and only HSE's. Narrowing role tasks by area means work
    whose area nobody covers would otherwise reach nobody — so it goes to HSE,
    and this is the number that says how much of it there is. It is the pressure
    to finish the mapping, and it is the honest alternative to a silent drop.
    """
    mine = _user_id(user)
    roles = await notify.roles_held_by(db, mine, user.get("role", ""))
    # ★ HSE's question, and gated as one. Reading corrective actions is a much
    # wider permission than being answerable for the plant, and this answer
    # names documents.
    if not roles & area_scope.SITE_WIDE_ROLES:
        return UnattributedOut(count=0, examples=[])

    rows = (await db.execute(
        select(Task).where(
            Task.assigned_user_id.is_(None),
            Task.is_completed.is_(False),
            Task.document_type.like("ehs%"),
        ).order_by(Task.created_at.desc())
    )).scalars().all()

    _, orphans = await area_scope.split_by_area(
        db, list(rows), user_id=mine, roles=roles)
    return UnattributedOut(
        count=len(orphans),
        examples=[t.title or t.document_number for t in orphans[:5]])


@router.get("/mine/tasks", response_model=list[AssignedTaskOut])
async def my_tasks(db: SessionDep, user: CanReportIncident):
    """Everything else assigned to me by this module.

    "My Actions" listed corrective actions only, and every other thing the
    module hands somebody — a toolbox talk to deliver, an inspection due, a
    policy to sign — went to the `tasks` table and appeared nowhere on the page
    named after being assigned work. The first acceptance round found it on the
    toolbox talk: the tasks were created correctly, eleven of them, and the
    supervisor had no screen that showed them.
    """
    # ★ Named to me, or given to a role I hold and to nobody in particular.
    #
    # Only the first half existed, and `assigned_role` was written by every
    # caller in the module and read by none of them — so a task raised for "the
    # HSE Manager" rather than for a person reached no screen at all. On the
    # test database that was 78 open tasks invisible to everybody: thirty-four
    # incidents awaiting review, twenty-two inspections due, six SDS reviews,
    # four JHSC items. Each of them is the failure this page exists to prevent,
    # and the page was the one hiding them.
    mine = _user_id(user)
    roles = await notify.roles_held_by(db, mine, user.get("role", ""))
    # ★ HSE is answerable for area work nobody has been named for, so the query
    # has to fetch it before the scoping can hand it to them. It reaches them
    # only if it is unowned — `split_by_area` decides that; without this the
    # rows never get as far as being decided about.
    # ★ And anybody who is down for an area, whatever role they hold. HSE's
    # answer in round 4 was "the area owners can be assigned to anyone
    # regardless of role. remove the supervisor requirement" — which cannot be
    # done by deleting the check that enforced it. Area work is addressed to
    # `area_supervisor`, and this query only fetched role tasks for holders of
    # that role, so an area given to somebody without it would have looked
    # covered while never reaching them: the silent loss the whole mechanism
    # exists to remove. Being named for an area is now its own reason to be
    # sent that area's work, and `split_by_area` still decides which areas.
    area_owner = bool(await area_scope.areas_for(db, mine))
    query_roles = roles | (area_scope.AREA_SCOPED_ROLES
                           if (roles & area_scope.SITE_WIDE_ROLES) or area_owner
                           else set())

    # ★ The actions already listed above this, so their own task is not shown
    # twice. It used to drop every task on a corrective action, which also
    # dropped the one that matters most to somebody who is not the owner: the
    # overdue escalation. A supervisor is sent "Overdue 12d: …" about their
    # report's action, is not the owner, and so had it excluded from the only
    # page that would have told them.
    owned = set((await db.execute(
        select(Action.id).where(Action.owner_id == mine,
                                Action.status != "closed"))).scalars().all())

    rows = (await db.execute(
        select(Task).where(
            or_(Task.assigned_user_id == mine,
                and_(Task.assigned_user_id.is_(None),
                     Task.assigned_role.in_(query_roles) if query_roles else false())),
            Task.is_completed.is_(False),
            Task.document_type.like("ehs%"),
            not_(and_(Task.document_type == task_service.DOC_ACTION,
                      Task.document_id.in_(owned) if owned else false())),
        ).order_by(Task.created_at.desc())
    )).scalars().all()

    # ★ Narrowed to the areas this person is down for. Work given to a role is
    # given to everybody holding it, and for `area_supervisor` that is one
    # plant-wide queue — a queue everybody can see is a queue nobody owns.
    # Work whose area nobody is down for goes to HSE and is counted, rather
    # than being shown to every supervisor or to no one.
    rows, _unattributed = await area_scope.split_by_area(
        db, list(rows), user_id=mine, roles=roles)

    out = []
    for t in rows:
        out.append(AssignedTaskOut(
            id=t.id, type=t.type, title=t.title, description=t.description,
            priority=t.priority, document_type=t.document_type,
            document_id=t.document_id, document_number=t.document_number,
            created_at=t.created_at,
            path=task_service.path_for(t),
        ))
    return out


class TaskPathOut(BaseModel):
    path: str


@router.get("/tasks/{task_id}/open", response_model=TaskPathOut)
async def where_a_task_opens(task_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    """Where a Safety task opens, for the Portal's inbox.

    ★ Round 7, TC-03: the Portal kept its own short list of where Safety
    documents live — incidents, actions, certificates — and sent every other
    Safety task (a session to run, a policy to sign, a toolbox talk) to a page
    that does not exist. It now asks here, so there is one answer. Only a
    location is returned; the page it names applies its own permission.
    """
    task = await db.get(Task, task_id)
    if task is None or not (task.document_type or "").startswith("ehs_"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such Safety task")
    return TaskPathOut(path=task_service.path_for(task) or "/actions/mine")


class HistoryEntry(BaseModel):
    at: datetime
    what: str          # raised | update | reassigned | verified | not_effective
    by: str | None
    text: str | None
    new_status: str | None = None


class ActionHistory(BaseModel):
    id: uuid.UUID
    action_no: str
    title: str
    status: str
    owner_name: str | None
    entries: list[HistoryEntry]


@router.get("/history", response_model=list[ActionHistory])
async def history_for_source(
    db: SessionDep, user: CanReadAction,  # noqa: ARG001
    source_type: str | None = None, source_id: uuid.UUID | None = None,
    action_id: list[uuid.UUID] = Query(default=[]),
):
    """Every corrective action raised from a record, with everything that
    happened to each — raised, progress, reassigned, verified.

    ★ Round 12, JHSC-01: "the history that the capa has gone through needs to
    be recorded on the history of the record (form) that created it."
    Round 4 had left it on the action; HSE want it on the record too.
    By source, or by id for a JHSC recommendation, whose actions' source is
    the meeting.
    """
    from app.models.action import ActionUpdate, ActionVerification
    if action_id:
        q = select(Action).where(Action.id.in_(action_id))
    elif source_type and source_id:
        q = select(Action).where(Action.source_type == source_type, Action.source_id == source_id)
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Name the record, or the actions")
    actions = list((await db.execute(q.order_by(Action.created_at))).scalars())
    if not actions:
        return []
    ids = [a.id for a in actions]
    updates = (await db.execute(select(ActionUpdate).where(
        ActionUpdate.action_id.in_(ids)).order_by(ActionUpdate.created_at))).scalars().all()
    checks = (await db.execute(select(ActionVerification).where(
        ActionVerification.action_id.in_(ids)))).scalars().all()
    out = []
    for a in actions:
        entries = [HistoryEntry(at=a.created_at, what="raised", by=None,
                                text=f"Raised for {a.owner_name or 'nobody yet'}, due "
                                     f"{a.due_date:%-d %b %Y}")]
        for u in updates:
            if u.action_id == a.id:
                entries.append(HistoryEntry(
                    at=u.created_at, by=u.author_name, text=u.body, new_status=u.new_status,
                    what="reassigned" if u.body.startswith(REASSIGNED) else "update"))
        for v in checks:
            if v.action_id == a.id:
                entries.append(HistoryEntry(
                    at=v.verified_at, by=v.verified_by_name, text=v.evidence,
                    what="verified" if v.is_effective else "not_effective"))
        entries.sort(key=lambda e: e.at)
        out.append(ActionHistory(id=a.id, action_no=a.action_no, title=a.title,
                                 status=a.status, owner_name=a.owner_name, entries=entries))
    return out


REASSIGNED = "Reassigned from "


class ReassignIn(BaseModel):
    owner_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=500)


@router.post("/{action_id}/reassign", response_model=ActionDetail)
async def reassign_action(action_id: uuid.UUID, payload: ReassignIn, db: SessionDep,
                          user: CanWriteAction):
    """Give the work to somebody else.

    ★ Round 12, INC-02/03 and Q7: "only the supervisor or HSE role can
    reassign CAPA's." Held to ehs.action.write — HSE, the coordinator and
    area supervisors. The open "do this" task moves with it; the change, from
    whom to whom and why, goes into the action's history (and so onto the
    record it was raised from).
    """
    from app.models.mirrors import User
    action = await crud.get(db, action_id)
    if action.status in ("closed", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Action {action.action_no} is {action.status}")
    to = await db.get(User, payload.owner_id)
    if to is None or not to.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick somebody still here")
    if to.id == action.owner_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"{to.full_name} already has it")
    me = _user_id(user)
    before = action.owner_name or "nobody"
    now = datetime.now(timezone.utc)
    for t in (await db.execute(select(Task).where(
            Task.document_type == task_service.DOC_ACTION, Task.document_id == action.id,
            Task.type == task_service.TASK_DO_ACTION, Task.is_completed.is_(False)))).scalars().all():
        t.is_completed, t.completed_at, t.completed_by = True, now, me
        await task_service.open_task(
            db, task_type=t.type, doc_type=t.document_type, doc_id=t.document_id,
            doc_number=t.document_number, title=t.title, assigned_role=t.assigned_role,
            assigned_user_id=to.id, description=t.description, priority=t.priority)
    action.owner_id, action.owner_name = to.id, to.full_name
    from app.models.action import ActionUpdate
    db.add(ActionUpdate(
        id=uuid.uuid4(), action_id=action.id, author_id=me,
        author_name=(await db.execute(select(User.full_name).where(User.id == me))).scalar(),
        body=f"{REASSIGNED}{before} to {to.full_name}: {payload.reason.strip()}",
        file_ids=[], new_status=None))
    await db.flush()
    return await _detail(db, action)


@router.post("/tasks/{task_id}/reviewed", status_code=status.HTTP_204_NO_CONTENT)
async def mark_reviewed(task_id: uuid.UUID, db: SessionDep, user: CanReadAction):
    """Round 12, Q5: HSE has reviewed the record whose actions all closed."""
    from fastapi import Response
    task = await db.get(Task, task_id)
    if task is None or task.type != task_service.TASK_REVIEW_CAPAS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such review")
    roles = await notify.roles_held_by(db, _user_id(user), user.get("role", ""))
    if task.assigned_role not in roles and task.assigned_user_id != _user_id(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This review is HSE's")
    if not task.is_completed:
        task.is_completed, task.completed_at = True, datetime.now(timezone.utc)
        task.completed_by = _user_id(user)
        await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{action_id}", response_model=ActionDetail)
async def get_action(action_id: uuid.UUID, db: SessionDep, user: CanReadAction):
    action = await crud.get(db, action_id)
    if action.owner_id != _user_id(user) and not await _may_browse(db, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only open corrective actions assigned to you",
        )
    return await _detail(db, action)


@router.post("/{action_id}/updates", response_model=ActionDetail)
async def add_update(
    action_id: uuid.UUID, payload: ActionUpdateIn, db: SessionDep, user: CanReadAction,
):
    """Progress notes and evidence.

    Gated on read rather than write: the person doing the work has to be able
    to report on it, and they are frequently not the person who may raise or
    reassign actions.
    """
    action = await crud.get(db, action_id)
    if action.owner_id != _user_id(user) and not await _may_browse(db, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only report progress on actions assigned to you",
        )
    await crud.add_update(db, action, payload, user_id=_user_id(user))
    return await _detail(db, action)


@router.post("/{action_id}/verify", response_model=ActionDetail)
async def verify_action(
    action_id: uuid.UUID, payload: ActionVerifyIn, db: SessionDep, user: CanReadAction,
):
    """Confirm the control is effective — or that it is not, which reopens it.

    Who may: anyone holding `ehs.action.verify` — HSE, the coordinator, area
    supervisors and committee members — for any action, and the action's own
    owner for theirs. Both halves come from HSE's answer in the second
    acceptance round, and the second half is why this is a rule here rather
    than only a permission: it depends on the row.

    It is a change of principle and worth naming. The module was built so that
    whoever did the work could not sign it off — the second-person check COR
    and ISO 45001 look for. HSE asked for the owner to be able to, so an owner
    may. The verification stores who performed it beside who owned the action
    and the audit pack shows both, so a self-verification reads as one rather
    than passing for independent.
    """
    action = await crud.get(db, action_id)
    me = _user_id(user)
    # ★ Round 12, Q7: "verification of CAPA's can only be completed by
    # supervisors or HSE." The owner verifying their own (round 2) is gone.
    if not await has_permission(db, me, user.get("role", ""), "ehs.action.verify"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "A corrective action is verified by a supervisor or HSE")
    await crud.verify(db, action, payload, user_id=me,
                      now=datetime.now(timezone.utc))
    return await _detail(db, action)
