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
from app.core.permissions import CanReadMedical
from app.api.v1.incidents import _user_id
from app.crud import incident as crud
from app.crud.incident import _resolve_name
from app.models.medical import IncidentMedical

router = APIRouter(prefix="/incidents/{incident_id}/medical")


class TimelineEntry(BaseModel):
    time: str = Field(default="", max_length=50)
    event: str = Field(default="", max_length=1000)


class MedicalIn(BaseModel):
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

    timeline: list[TimelineEntry] = Field(default_factory=list)


class MedicalOut(MedicalIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    recorded_by_name: str | None


@router.get("", response_model=MedicalOut | None)
async def get_medical(incident_id: uuid.UUID, db: SessionDep, user: CanReadMedical):  # noqa: ARG001
    await crud.get(db, incident_id)  # 404s on an unknown incident
    return (await db.execute(
        select(IncidentMedical).where(IncidentMedical.incident_id == incident_id)
    )).scalar_one_or_none()


@router.put("", response_model=MedicalOut)
async def upsert_medical(
    incident_id: uuid.UUID, payload: MedicalIn, db: SessionDep, user: CanReadMedical,
):
    incident = await crud.get(db, incident_id)
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
    for field, value in data.items():
        setattr(row, field, value)

    row.recorded_by = _user_id(user)
    row.recorded_by_name = await _resolve_name(db, row.recorded_by)
    await db.flush()
    await db.refresh(row)
    return row
