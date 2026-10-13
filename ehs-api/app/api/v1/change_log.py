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
from app.models.incident import Incident, IncidentPerson
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
    user: CanReadIncident,
    table: str = Query(description="The table the record lives in"),
    row_id: str = Query(description="The record", max_length=64),
    limit: int = Query(default=200, le=500),
):
    """Newest first, because the question is usually "what just happened"."""
    hidden_reporter = await _anonymous_reporter(db, user, table, row_id)
    rows = (await db.execute(
        select(ChangeLogEntry, User.full_name)
        .outerjoin(User, User.id == ChangeLogEntry.changed_by)
        .where(ChangeLogEntry.table_name == table, ChangeLogEntry.row_id == row_id)
        .order_by(ChangeLogEntry.changed_at.desc(), ChangeLogEntry.field)
        .limit(limit)
    )).all()
    out = []
    for entry, name in rows:
        who = entry.changed_by_name or name
        old_value, new_value = entry.old_value, entry.new_value
        if hidden_reporter is not None:
            if entry.changed_by == hidden_reporter:
                who = "The reporter (anonymous)"
            if entry.field in IDENTIFYING_FIELDS:
                old_value = new_value = "(hidden — anonymous report)" if (
                    old_value or new_value) else None
        out.append(ChangeOut(
            id=entry.id, action=entry.action, field=entry.field,
            old_value=old_value, new_value=new_value,
            # The snapshot where there is one, the live name otherwise. The id
            # is what ties the row to a person; this is how it is read back.
            changed_by_name=who,
            changed_at=entry.changed_at.isoformat(), row_ref=entry.row_ref,
        ))
    return out


# Fields that name the person who filed a report.
IDENTIFYING_FIELDS = {"reported_by", "reported_by_name", "signature", "signer_name",
                      "signer_role"}


async def _anonymous_reporter(db, user: dict, table: str, row_id: str) -> uuid.UUID | None:
    """The reporter to hide, when this trail belongs to an anonymous report and
    the reader is not HSE.

    ★ Round 6, box 4: "only the HSE role can see what user ID was used to submit
    the incident." The trail of an anonymous report named its reporter on the
    line that created it, and carried their user id as the value of
    reported_by, to anybody who can read incidents.
    """
    from app.api.v1.incidents import sees_anonymous_reporters

    incident_id = None
    try:
        rid = uuid.UUID(row_id)
    except ValueError:
        return None
    if table == "ehs_incidents":
        incident_id = rid
    elif table == "ehs_incident_persons":
        person = await db.get(IncidentPerson, rid)
        incident_id = person.incident_id if person else None
    if incident_id is None:
        return None
    incident = await db.get(Incident, incident_id)
    if incident is None or not incident.is_anonymous:
        return None
    if await sees_anonymous_reporters(db, user):
        return None
    return incident.reported_by or uuid.UUID(int=0)
