"""Reading the trail for one record.

Gated on `ehs.incident.read`, the same key the evidence pack uses and the one
the built-in auditor role already holds — this is the same question asked of a
single form rather than of the whole year.

Values from the medical tables are absent by design, not missing: see
services/change_log.py. The field name and the time still answer "was this
altered, by whom, when", which is what the trail is for.
"""
import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import SessionDep
from app.core.permissions import CanReadIncident
from app.models.change_log import ChangeLogEntry
from app.models.mirrors import User

router = APIRouter(prefix="/change-log")


class ChangeOut(BaseModel):
    id: uuid.UUID
    action: str
    field: str | None
    old_value: str | None
    new_value: str | None
    changed_by_name: str | None
    changed_at: str
    row_ref: str | None


@router.get("", response_model=list[ChangeOut])
async def read_trail(
    db: SessionDep,
    user: CanReadIncident,  # noqa: ARG001
    table: str = Query(description="The table the record lives in"),
    row_id: str = Query(description="The record", max_length=64),
    limit: int = Query(default=200, le=500),
):
    """Newest first, because the question is usually "what just happened"."""
    rows = (await db.execute(
        select(ChangeLogEntry, User.full_name)
        .outerjoin(User, User.id == ChangeLogEntry.changed_by)
        .where(ChangeLogEntry.table_name == table, ChangeLogEntry.row_id == row_id)
        .order_by(ChangeLogEntry.changed_at.desc(), ChangeLogEntry.field)
        .limit(limit)
    )).all()
    return [
        ChangeOut(
            id=entry.id, action=entry.action, field=entry.field,
            old_value=entry.old_value, new_value=entry.new_value,
            # The snapshot where there is one, the live name otherwise. The id
            # is what ties the row to a person; this is how it is read back.
            changed_by_name=entry.changed_by_name or name,
            changed_at=entry.changed_at.isoformat(), row_ref=entry.row_ref,
        )
        for entry, name in rows
    ]
