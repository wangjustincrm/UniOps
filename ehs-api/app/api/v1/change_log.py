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
from app.db.base import Base
from app.models.incident import Incident
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
    hidden = await _anonymous_reporter(db, user, table, row_id)
    hidden_reporter = hidden[0] if hidden else None
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
            # ★ Any value that names them, whatever the field: the uploader of
            # a photo, the person on a child row. Their id and their name.
            leaks = [v for v in hidden[1:] if v]
            if entry.field in IDENTIFYING_FIELDS or any(
                    k.lower() in (v or "").lower() for k in leaks for v in (old_value, new_value)):
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


async def _anonymous_reporter(
        db, user: dict, table: str, row_id: str) -> tuple[uuid.UUID, str, str | None] | None:
    """The reporter to hide, when this trail belongs to an anonymous report and
    the reader is not HSE.

    ★ Round 6, box 4: "only the HSE role can see what user ID was used to submit
    the incident." The trail of an anonymous report named its reporter on the
    line that created it, and carried their user id as the value of
    reported_by, to anybody who can read incidents.

    Every table with an `incident_id` belongs to the same report — the
    attachments, the investigation, the medical form — so each is masked by
    its incident. ★ Only the persons table was, and a photo the reporter
    uploaded named them on its trail. The table is looked up in the models'
    metadata, never put into SQL as text.

    Returns the reporter's id, that id as text, and their name: the values to
    hide wherever they appear.
    """
    from app.api.v1.incidents import sees_anonymous_reporters

    incident_id = None
    try:
        rid = uuid.UUID(row_id)
    except ValueError:
        return None
    if table == "ehs_incidents":
        incident_id = rid
    else:
        t = Base.metadata.tables.get(table)
        # Two ways a record hangs on a report: an `incident_id`, or a
        # `source_id` (risk assessments, actions, deadlines, forms). ★ A
        # source_id is judged by whether it names an incident, not by its
        # source_type — the same rule risk._may_see learned the hard way.
        link = ("incident_id" if t is not None and "incident_id" in t.c
                else "source_id" if t is not None and "source_id" in t.c else None)
        if link is None or "id" not in t.c:
            return None
        incident_id = (await db.execute(
            select(t.c[link]).where(t.c.id == rid))).scalar_one_or_none()
        if incident_id is None:
            # A row since deleted — a photo removed — keeps its trail, and the
            # trail recorded which incident it belonged to.
            logged = (await db.execute(
                select(ChangeLogEntry.new_value, ChangeLogEntry.old_value)
                .where(ChangeLogEntry.table_name == table, ChangeLogEntry.row_id == row_id,
                       ChangeLogEntry.field == link)
                .limit(1))).first()
            try:
                incident_id = uuid.UUID(next(v for v in logged if v)) if logged else None
            except (StopIteration, ValueError):
                incident_id = None
    if incident_id is None:
        return None
    incident = await db.get(Incident, incident_id)
    if incident is None or not incident.is_anonymous:
        return None
    if await sees_anonymous_reporters(db, user):
        return None
    reporter = incident.reported_by or uuid.UUID(int=0)
    name = (await db.get(User, reporter)) if incident.reported_by else None
    return reporter, str(reporter), (name.full_name if name else None) or incident.reported_by_name
