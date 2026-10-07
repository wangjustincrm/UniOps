"""The hazard register and job safety analyses.

Permissions reuse the corrective-action keys rather than adding two more pairs.
A hazard is the same kind of object to the same people — something identified,
owned, and worked on — and every role that may raise or manage a CAPA is
exactly the set that may raise or manage a hazard. Two more keys would be two
more things to get wrong in the matrix, and they would drift.

Signing a JSA is the exception: every worker signs one before starting a job,
so it is gated on the key everybody holds.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanReadAction, CanReportIncident, CanWriteAction
from app.crud import cause as cause_crud
from app.schemas.action import ActionOut
from app.schemas.investigation import CausesReplace
from app.crud import hazard as crud

router = APIRouter()

_CONTROL_LEVELS = "^(elimination|substitution|engineering|administrative|ppe)$"


# ── Hazards ──────────────────────────────────────────────────────────────────

class HazardIn(BaseModel):
    # A key this model does not know is a mistake, not something to drop
    # quietly: a misspelled field name would otherwise read as "the user
    # cleared it" and overwrite a good value with nothing.
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=250)
    description: str | None = None
    # What was found when somebody went and looked, as distinct from what the
    # hazard is.
    findings: str | None = None
    hazard_type_id: uuid.UUID | None = None
    hazard_type_label: str | None = Field(default=None, max_length=200)
    location_id: uuid.UUID | None = None
    equipment_id: uuid.UUID | None = None
    existing_controls: str | None = None
    # Where the control sits on the hierarchy. "We gave them gloves" reads
    # differently beside "we removed the pinch point", and an auditor asks.
    control_level: str | None = Field(default=None, pattern=_CONTROL_LEVELS)
    owner_id: uuid.UUID | None = None
    identified_on: date | None = None
    review_due_on: date | None = None
    source_type: str | None = Field(default=None, max_length=20)
    source_id: uuid.UUID | None = None


class HazardPatch(HazardIn):
    title: str | None = Field(default=None, max_length=250)
    # open | controlled | eliminated. `controlled` is not closed: a guarded
    # pinch point is still a pinch point and comes back round for review.
    status: str | None = Field(default=None, pattern="^(open|controlled|eliminated)$")


class HazardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hazard_no: str
    title: str
    description: str | None
    findings: str | None = None
    created_by: uuid.UUID | None = None
    created_by_name: str | None = None
    # Both halves: the label is the snapshot that keeps an old record readable
    # after the vocabulary is renamed, the id is what puts the picker back on
    # the right entry when the record is reopened for correction. Returning
    # only the label meant editing a hazard silently cleared its kind.
    hazard_type_id: uuid.UUID | None
    hazard_type_label: str | None
    location_id: uuid.UUID | None
    location_path: str | None
    department_name: str | None
    equipment_id: uuid.UUID | None
    risk_assessment_id: uuid.UUID | None
    current_rating: str | None
    residual_rating: str | None
    existing_controls: str | None
    control_level: str | None
    owner_id: uuid.UUID | None
    owner_name: str | None
    identified_on: date | None
    review_due_on: date | None
    status: str
    created_at: datetime


class RatingCount(BaseModel):
    rating: str
    count: int


@router.get("/hazards", response_model=list[HazardOut])
async def list_hazards(
    db: SessionDep, user: CanReadAction,  # noqa: ARG001
    status_in: list[str] | None = Query(default=None, alias="status"),
    location_id: uuid.UUID | None = None,
    rating: str | None = None,
):
    """Ranked worst first — the register's only real use is as that list."""
    return await crud.list_hazards(
        db, status_in=status_in, location_id=location_id, rating=rating)


class HazardCauseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cause_type: str
    vocabulary_item_id: uuid.UUID | None
    label: str
    note: str | None
    sort_order: int


class HazardCauseTree(BaseModel):
    """The register entry's causes, split the way the screen shows them, with
    the actions raised against each one.

    Same shape as the investigation's tree, because it is the same question asked
    of a different record — and a root cause with nothing under it has to read as
    visibly incomplete in both places.
    """

    hazard_no: str
    immediate: list[HazardCauseOut]
    root: list[HazardCauseOut]
    actions: dict[uuid.UUID, list[ActionOut]]


@router.get("/hazards/{hazard_id}/causes", response_model=HazardCauseTree)
async def hazard_causes(hazard_id: uuid.UUID, db: SessionDep, user: CanReadAction):  # noqa: ARG001
    hazard = await crud.get_hazard(db, hazard_id)
    rows = await cause_crud.list_for(db, hazard_id=hazard.id)
    grouped = await cause_crud.actions_by_cause(
        db, source_type="hazard", source_id=hazard.id)
    return HazardCauseTree(
        hazard_no=hazard.hazard_no,
        immediate=[HazardCauseOut.model_validate(c) for c in rows
                   if c.cause_type == "immediate"],
        root=[HazardCauseOut.model_validate(c) for c in rows if c.cause_type == "root"],
        actions={cid: [ActionOut.model_validate(a) for a in acts]
                 for cid, acts in grouped.items()},
    )


@router.put("/hazards/{hazard_id}/causes", response_model=HazardCauseTree)
async def replace_hazard_causes(
    hazard_id: uuid.UUID, payload: CausesReplace, db: SessionDep, user: CanWriteAction,
):
    """Record what caused it, and raise the work in the same request.

    ★ Round 5, answer box 11. The action travels with the cause for the reason it
    does on an investigation: an action points at a cause and a cause has no id
    until it is saved, so doing it in two steps from the client means guessing
    which id belongs to which of the rows just typed.
    """
    hazard = await crud.get_hazard(db, hazard_id)
    await cause_crud.replace(
        db,
        causes=payload.causes,
        source_type="hazard",
        source_id=hazard.id,
        source_ref=hazard.hazard_no,
        hazard_id=hazard.id,
    )
    return await hazard_causes(hazard_id, db, user)


@router.get("/hazards/summary", response_model=list[RatingCount])
async def hazard_summary(db: SessionDep, user: CanReadAction):  # noqa: ARG001
    return await crud.hazard_summary(db)


@router.post("/hazards", response_model=HazardOut, status_code=status.HTTP_201_CREATED)
async def create_hazard(payload: HazardIn, db: SessionDep, user: CanWriteAction):
    return await crud.create_hazard(db, payload.model_dump(), creator_id=_user_id(user))


@router.get("/hazards/{hazard_id}", response_model=HazardOut)
async def get_hazard(hazard_id: uuid.UUID, db: SessionDep, user: CanReadAction):  # noqa: ARG001
    return await crud.get_hazard(db, hazard_id)


@router.patch("/hazards/{hazard_id}", response_model=HazardOut)
async def update_hazard(
    hazard_id: uuid.UUID, payload: HazardPatch, db: SessionDep, user: CanWriteAction,  # noqa: ARG001
):
    hazard = await crud.get_hazard(db, hazard_id)
    return await crud.update_hazard(db, hazard, payload.model_dump(exclude_unset=True))


class AssessIn(BaseModel):
    """Assessing a hazard against HSE's matrix.

    The rating is not accepted from the client — it is computed from the grid,
    which is the company's own document. What is chosen is the likelihood, the
    severity, and which consequence the severity was judged on.
    """

    consequence_type: str = Field(default="people")
    likelihood_code: str = Field(max_length=4)
    severity_level: int = Field(ge=0, le=5)
    controls: str | None = None
    residual_likelihood_code: str | None = Field(default=None, max_length=4)
    residual_severity_level: int | None = Field(default=None, ge=0, le=5)


@router.post("/hazards/{hazard_id}/assess", response_model=HazardOut)
async def assess_hazard(
    hazard_id: uuid.UUID, payload: AssessIn, db: SessionDep, user: CanWriteAction,
):
    """Score it through the shared risk service and make that the current one."""
    from app.api.v1.risk import AssessmentIn, create_assessment

    hazard = await crud.get_hazard(db, hazard_id)
    assessment = await create_assessment(
        AssessmentIn(
            source_type="hazard", source_id=hazard.id,
            hazard_id=hazard.hazard_type_id, hazard_label=hazard.title,
            description=hazard.description,
            **payload.model_dump(),
        ),
        db, user,
    )
    return await crud.attach_assessment(db, hazard, assessment)


# ── Job safety analysis ──────────────────────────────────────────────────────

class JsaIn(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    job_description: str | None = None
    location_id: uuid.UUID | None = None
    equipment_id: uuid.UUID | None = None
    required_ppe: str | None = None
    review_due_on: date | None = None


class StepIn(BaseModel):
    step: str = Field(min_length=1)
    hazards: str | None = None
    controls: str | None = None
    control_level: str | None = Field(default=None, pattern=_CONTROL_LEVELS)
    hazard_id: uuid.UUID | None = None
    residual_rating: str | None = Field(default=None, max_length=20)


class StepsIn(BaseModel):
    steps: list[StepIn] = Field(default_factory=list, max_length=100)


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    step: str
    hazards: str | None
    controls: str | None
    control_level: str | None
    hazard_id: uuid.UUID | None
    residual_rating: str | None


class JsaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    jsa_no: str
    title: str
    job_description: str | None
    version: int
    location_id: uuid.UUID | None
    location_path: str | None
    department_name: str | None
    equipment_id: uuid.UUID | None
    required_ppe: str | None
    status: str
    approved_at: datetime | None
    approved_by_name: str | None
    review_due_on: date | None
    supersedes_id: uuid.UUID | None
    created_by_name: str | None
    created_at: datetime


class SignerIn(BaseModel):
    user_id: uuid.UUID | None = None
    person_name: str = Field(min_length=1, max_length=255)
    person_role: str | None = Field(default=None, max_length=150)
    signature: str | None = None
    is_supervisor: bool = False


class SignIn(BaseModel):
    signers: list[SignerIn] = Field(min_length=1)
    # The day the job is done, not the day the record is typed up.
    worked_on: date


class SignoffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    user_id: uuid.UUID | None
    person_name: str
    person_role: str | None
    worked_on: date
    signed_at: datetime
    is_supervisor: bool


@router.get("/jsa", response_model=list[JsaOut])
async def list_jsa(
    db: SessionDep, user: CanReportIncident,  # noqa: ARG001
    status_in: list[str] | None = Query(default=None, alias="status"),
):
    return await crud.list_jsa(db, status_in=status_in)


@router.post("/jsa", response_model=JsaOut, status_code=status.HTTP_201_CREATED)
async def create_jsa(payload: JsaIn, db: SessionDep, user: CanWriteAction):
    return await crud.create_jsa(db, payload.model_dump(), creator_id=_user_id(user))


@router.get("/jsa/{jsa_id}", response_model=JsaOut)
async def get_jsa(jsa_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    return await crud.get_jsa(db, jsa_id)


@router.patch("/jsa/{jsa_id}", response_model=JsaOut)
async def update_jsa(
    jsa_id: uuid.UUID, payload: JsaIn, db: SessionDep, user: CanWriteAction,  # noqa: ARG001
):
    jsa = await crud.get_jsa(db, jsa_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(jsa, key, value)
    await db.flush()
    return jsa


@router.get("/jsa/{jsa_id}/steps", response_model=list[StepOut])
async def get_steps(jsa_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    await crud.get_jsa(db, jsa_id)
    return await crud.steps_for(db, jsa_id)


@router.put("/jsa/{jsa_id}/steps", response_model=list[StepOut])
async def put_steps(
    jsa_id: uuid.UUID, payload: StepsIn, db: SessionDep, user: CanWriteAction,  # noqa: ARG001
):
    """Replace the step list. Only while it is a draft."""
    jsa = await crud.get_jsa(db, jsa_id)
    return await crud.replace_steps(db, jsa, [s.model_dump() for s in payload.steps])


@router.post("/jsa/{jsa_id}/activate", response_model=JsaOut)
async def activate_jsa(jsa_id: uuid.UUID, db: SessionDep, user: CanWriteAction):
    """Approve it for use. Refused if a step names a hazard with no control."""
    jsa = await crud.get_jsa(db, jsa_id)
    return await crud.activate(db, jsa, user_id=_user_id(user))


@router.post("/jsa/{jsa_id}/new-version", response_model=JsaOut,
             status_code=status.HTTP_201_CREATED)
async def new_jsa_version(jsa_id: uuid.UUID, db: SessionDep, user: CanWriteAction):
    """A draft copy, steps included, that retires this one when approved."""
    jsa = await crud.get_jsa(db, jsa_id)
    return await crud.new_jsa_version(db, jsa, creator_id=_user_id(user))


@router.get("/jsa/{jsa_id}/signoffs", response_model=list[SignoffOut])
async def get_signoffs(
    jsa_id: uuid.UUID, db: SessionDep, user: CanReportIncident,  # noqa: ARG001
    worked_on: date | None = None,
):
    await crud.get_jsa(db, jsa_id)
    return await crud.signoffs_for(db, jsa_id, worked_on=worked_on)


@router.post("/jsa/{jsa_id}/sign", response_model=list[SignoffOut],
             status_code=status.HTTP_201_CREATED)
async def sign_jsa(
    jsa_id: uuid.UUID, payload: SignIn, db: SessionDep, user: CanReportIncident,  # noqa: ARG001
):
    """The crew signs before starting — several at once, from one device."""
    jsa = await crud.get_jsa(db, jsa_id)
    return await crud.sign(
        db, jsa, [s.model_dump() for s in payload.signers], worked_on=payload.worked_on)
