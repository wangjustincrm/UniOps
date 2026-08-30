"""Every clock in the module, in one list.

The compliance calendar reads this. Statutory notifications, certificate
expiries and periodic reviews are the same kind of object here, which is what
lets one screen show all of them with the regulation each comes from.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.deps import SessionDep
from app.core.permissions import CanReadIncident
from app.models.statutory import StatutoryDeadline

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
    return [DeadlineRow.model_validate(r) for r in rows]
