"""Section B — injury and LTI detail, behind its own permission.

Every endpoint here requires `ehs.incident.medical.read`, which is narrower than
reading the incident itself. Someone who can see that a colleague was hurt has
no business seeing their allergies, their medication or their medical history,
and HSE's own form is headed `Private` for the same reason.

Read and write share the one permission on purpose: a person trusted to record
a first-aid assessment is the person trusted to read it back, and splitting them
would produce a role that can write a record it cannot check.
"""
import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.deps import SessionDep
from app.core.permissions import CanReportIncident
from app.api.v1.incidents import _user_id
from app.crud import incident as crud
from app.crud.incident import _resolve_name
from app.models.medical import IncidentMedical

router = APIRouter(prefix="/incidents/{incident_id}/medical")

SUPERVISOR_ROLES = ("area_supervisor", "supervisor")


async def may_read_section_b(db, user: dict) -> bool:
    """`ehs.incident.medical.read`, or — once HSE turn it on under Settings →
    Rules (UAT 13, question E) — holding a supervisor's role."""
    from uniops_authz import has_permission

    from app.models.config import EhsConfig
    from app.services.notify import roles_held_by
    uid = _user_id(user)
    if await has_permission(db, uid, user.get("role", ""), "ehs.incident.medical.read"):
        return True
    cfg = await db.get(EhsConfig, 1)
    if not (cfg and cfg.supervisors_see_section_b):
        return False
    return bool(set(SUPERVISOR_ROLES) & await roles_held_by(db, uid, user.get("role", "")))


async def _gate(db, user) -> None:
    if not await may_read_section_b(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Section B is open to HSE and first aiders only")


class TimelineEntry(BaseModel):
    time: str = Field(default="", max_length=50)
    event: str = Field(default="", max_length=1000)


class VitalsReading(BaseModel):
    """One set of observations, at one time.

    Everything but the pain score is free text on purpose: a blood pressure is
    written "120/80" and a pulse is sometimes "110 weak", and a numeric field
    would make a first aider drop the word that mattered.
    """

    at: str = Field(default="", max_length=50)
    pulse: str = Field(default="", max_length=20)
    respirations: str = Field(default="", max_length=20)
    blood_pressure: str = Field(default="", max_length=20)
    pain_level: int | None = Field(default=None, ge=0, le=10)
    pain_location: str = Field(default="", max_length=255)
    other: str = Field(default="", max_length=2000)


class MedicalIn(BaseModel):
    # ★ This endpoint writes every field it is given and blanks the ones it is
    # not: the screen sends the whole section each time, which is the right
    # shape for a PUT but leaves no defence against a key that is not
    # recognised. Dropping one quietly is how this module destroyed an
    # investigation — an unknown key looked like "the user cleared it". This
    # table holds allergies, medicines and past medical history, so the same
    # mistake here loses health information nobody can reconstruct.
    model_config = ConfigDict(extra="forbid")

    # Who the follow-up goes to. Both halves: the id is the link, the name is
    # the snapshot that survives the person leaving.
    supervisor_id: uuid.UUID | None = None
    supervisor_name: str | None = Field(default=None, max_length=255)
    type_of_injury: str | None = Field(default=None, max_length=255)

    # SAMPLE order. Kept in this order in the schema as well as the table, so a
    # generated form follows it too.
    assessed_at_text: str | None = Field(default=None, max_length=50)
    signs_symptoms: str | None = None
    allergies: str | None = None
    medicines: str | None = None
    past_medical_history: str | None = None
    last_meal: str | None = None
    events_prior: str | None = None
    general_comments: str | None = None

    vitals_at_text: str | None = Field(default=None, max_length=50)
    pulse: str | None = Field(default=None, max_length=20)
    respirations: str | None = Field(default=None, max_length=20)
    blood_pressure: str | None = Field(default=None, max_length=20)
    pain_level: int | None = Field(default=None, ge=0, le=10)
    pain_location: str | None = Field(default=None, max_length=255)
    vitals_other: str | None = None
    vitals_series: list[VitalsReading] = Field(default_factory=list)

    timeline: list[TimelineEntry] = Field(default_factory=list)


class MedicalOut(MedicalIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    recorded_by_name: str | None


async def _out(db, incident, row: IncidentMedical | None, user: dict) -> MedicalOut | None:
    """★ Review (uniops-8b): on an anonymous report the reporter often fills in
    this section right after sending it, so "recorded by" named them to every
    first aider who opened the incident. Withheld as on the other parts of the
    report, from everyone but HSE."""
    from app.api.v1.incidents import WITHHELD, withheld_reporter
    if row is None:
        return None
    out = MedicalOut.model_validate(row)
    hidden = await withheld_reporter(db, incident, user)
    if hidden is not None and row.recorded_by == hidden[0]:
        out.recorded_by_name = WITHHELD
    return out


@router.get("", response_model=MedicalOut | None)
async def get_medical(incident_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    await _gate(db, user)
    incident = await crud.get(db, incident_id)  # 404s on an unknown incident
    return await _out(db, incident, (await db.execute(
        select(IncidentMedical).where(IncidentMedical.incident_id == incident_id)
    )).scalar_one_or_none(), user)


@router.put("", response_model=MedicalOut)
async def upsert_medical(
    incident_id: uuid.UUID, payload: MedicalIn, db: SessionDep, user: CanReportIncident,
):
    await _gate(db, user)
    incident = await crud.get(db, incident_id)
    crud.assert_open(incident, "the injury detail")
    if incident.status == "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Submit the incident before recording injury detail",
        )

    row = (await db.execute(
        select(IncidentMedical).where(IncidentMedical.incident_id == incident_id)
    )).scalar_one_or_none()
    if row is None:
        row = IncidentMedical(id=uuid.uuid4(), incident_id=incident_id)
        db.add(row)

    data = payload.model_dump()
    data["timeline"] = [t for t in data["timeline"] if t.get("time") or t.get("event")]
    # A reading with nothing in it is the ordinary result of adding a row and
    # changing your mind, not an observation that everything was blank.
    data["vitals_series"] = [
        v for v in data["vitals_series"]
        if any(str(x).strip() for x in v.values() if x is not None)
    ]
    for field, value in data.items():
        setattr(row, field, value)

    row.recorded_by = _user_id(user)
    row.recorded_by_name = await _resolve_name(db, row.recorded_by)
    await db.flush()
    await db.refresh(row)
    return await _out(db, incident, row, user)


# Not under one incident: whether the person asking may open Section B at all,
# so the report and the incident page offer it exactly when the server allows.
access_router = APIRouter()


class SectionBAccess(BaseModel):
    may_read: bool


@access_router.get("/section-b/access", response_model=SectionBAccess)
async def section_b_access(db: SessionDep, user: CanReportIncident):
    return SectionBAccess(may_read=await may_read_section_b(db, user))
