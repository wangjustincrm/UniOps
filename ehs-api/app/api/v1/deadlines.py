"""Every clock in the module, in one list.

The compliance calendar reads this. Statutory notifications, certificate
expiries and periodic reviews are the same kind of object here, which is what
lets one screen show all of them with the regulation each comes from.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.deps import SessionDep
from app.core.permissions import CanManageStatutory, CanReadIncident
from app.models.statutory import StatutoryDeadline
from app.services import tasks as task_service
from app.services.deadlines import DEADLINE_DOC_TYPE

router = APIRouter()


class DeadlineRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: str
    source_id: uuid.UUID
    source_ref: str | None
    kind: str
    regulation_ref: str | None
    clock_type: str
    starts_at: datetime
    due_at: datetime
    satisfied_at: datetime | None
    # ★ Where this row opens. Round 4: "every item in the compliance calendar
    # should be clickable and take the user to that item." Only incidents were
    # links; a certificate expiry, an SDS review and a committee reply rendered
    # as plain rows, and the screen's own comment admitted it. Computed from the
    # same map the task inbox uses rather than from a second one on the client —
    # two maps of the same thing drift, and one of these paths changed this
    # round.
    path: str = ""
    escalation_level: int


@router.get("", response_model=list[DeadlineRow])
async def list_deadlines(
    db: SessionDep,
    user: CanReadIncident,  # noqa: ARG001
    include_satisfied: bool = Query(
        default=False,
        description="Discharged obligations. Off by default — the calendar is about what is still owed."),
    kind: str | None = None,
    overdue_only: bool = False,
    limit: int = Query(default=200, le=500),
):
    stmt = select(StatutoryDeadline)
    if not include_satisfied:
        stmt = stmt.where(StatutoryDeadline.satisfied_at.is_(None))
    if kind:
        stmt = stmt.where(StatutoryDeadline.kind == kind)
    if overdue_only:
        stmt = stmt.where(
            StatutoryDeadline.due_at < datetime.now(timezone.utc),
            StatutoryDeadline.satisfied_at.is_(None),
        )
    rows = (await db.execute(stmt.order_by(StatutoryDeadline.due_at).limit(limit))).scalars()

    out = []
    for r in rows:
        row = DeadlineRow.model_validate(r)
        doc_type = DEADLINE_DOC_TYPE.get(r.source_type)
        template = task_service.DOC_PATHS.get(doc_type) if doc_type else None
        row.path = template.format(id=r.source_id) if template and r.source_id else ""
        out.append(row)
    return out


class NotRequiredIn(BaseModel):
    reason: str = Field(min_length=5, max_length=480)


@router.post("/{deadline_id}/not-required", response_model=DeadlineRow)
async def not_required(
    deadline_id: uuid.UUID, payload: NotRequiredIn, db: SessionDep, user: CanManageStatutory,
):
    """Stop a clock that turned out not to apply, saying why.

    ★ Round 7. Closing an incident and signing off its review now wait for its
    Ministry and WSIB clocks — and a clock started on a first report can stop
    applying: the injury is reclassified as first aid, or the Ministry says it
    is not reportable. Without this the incident could never close. Recorded
    as discharged, with "Not required:" and the reason as its evidence, by the
    person who said so; its reminder task closes with it.
    """
    from app.api.v1.incidents import _user_id
    from app.models.mirrors import User
    from app.services import deadlines as deadline_service

    deadline = await db.get(StatutoryDeadline, deadline_id)
    if deadline is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such deadline")
    if deadline.satisfied_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This deadline is already closed")
    me = await db.get(User, _user_id(user))
    await deadline_service.satisfy(
        db, deadline, user_id=_user_id(user), user_name=me.full_name if me else None,
        evidence_note=f"Not required: {payload.reason.strip()}",
        when=datetime.now(timezone.utc))
    await db.flush()
    row = DeadlineRow.model_validate(deadline)
    return row
