"""Risk assessments against HSE's matrix.

The rating is computed here, not accepted from the client: the grid is the
company's document and an assessment that disagrees with it is worse than no
assessment. What the caller chooses is the likelihood, the severity, and which
consequence type the severity was judged on.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.v1.incidents import _may_read, _user_id
from uniops_authz import has_permission
from app.core.deps import SessionDep
from app.core.permissions import CanReportIncident
from app.crud.incident import _resolve_name
from app.models.incident import Incident
from app.models.risk import RiskAssessment, RiskLikelihood, RiskSeverity

router = APIRouter(prefix="/risk")

CONSEQUENCE_TYPES = ("people", "downtime", "reputation", "environmental", "asset_damage")


class LikelihoodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    label: str
    qualitative: str | None
    frequency: str | None
    ratings: dict


class SeverityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    level: int
    people: str | None
    downtime: str | None
    reputation: str | None
    environmental: str | None
    asset_damage: str | None


class MatrixOut(BaseModel):
    likelihood: list[LikelihoodOut]
    severity: list[SeverityOut]
    consequence_types: list[str]


class AssessmentIn(BaseModel):
    source_type: str | None = Field(default=None, max_length=20)
    source_id: uuid.UUID | None = None
    hazard_id: uuid.UUID | None = None
    hazard_label: str | None = Field(default=None, max_length=200)
    description: str | None = None
    consequence_type: str = Field(default="people")
    likelihood_code: str = Field(max_length=4)
    severity_level: int = Field(ge=0, le=5)
    controls: str | None = None
    residual_likelihood_code: str | None = Field(default=None, max_length=4)
    residual_severity_level: int | None = Field(default=None, ge=0, le=5)


class AssessmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: str | None
    source_id: uuid.UUID | None
    hazard_label: str | None
    description: str | None
    consequence_type: str
    likelihood_code: str
    likelihood_label: str | None
    severity_level: int
    severity_label: str | None
    rating: str
    controls: str | None
    residual_likelihood_code: str | None
    residual_severity_level: int | None
    residual_rating: str | None
    assessed_by_name: str | None
    assessed_at: datetime


@router.get("/matrix", response_model=MatrixOut)
async def get_matrix(db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    """The whole grid, so a client can render it and show what a choice means."""
    likelihood = list((await db.execute(
        select(RiskLikelihood).order_by(RiskLikelihood.sort_order))).scalars().all())
    severity = list((await db.execute(
        select(RiskSeverity).order_by(RiskSeverity.level))).scalars().all())
    return MatrixOut(likelihood=likelihood, severity=severity,
                     consequence_types=list(CONSEQUENCE_TYPES))


async def _rate(db: SessionDep, likelihood_code: str, severity_level: int) -> tuple[str, str]:
    row = await db.get(RiskLikelihood, likelihood_code)
    if row is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Unknown likelihood '{likelihood_code}'")
    rating = (row.ratings or {}).get(str(severity_level))
    if rating is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The matrix has no rating for likelihood {likelihood_code} at severity "
            f"{severity_level}",
        )
    return rating, row.label


@router.post("/assessments", response_model=AssessmentOut, status_code=201)
async def create_assessment(
    payload: AssessmentIn, db: SessionDep, user: CanReportIncident,
):
    if not await _may_see(db, user, payload.source_type, payload.source_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only assess incidents you reported or were involved in")
    if payload.consequence_type not in CONSEQUENCE_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown consequence type. One of: {', '.join(CONSEQUENCE_TYPES)}")

    rating, likelihood_label = await _rate(db, payload.likelihood_code, payload.severity_level)

    sev = await db.get(RiskSeverity, payload.severity_level)
    # Snapshot the wording for the consequence type actually used, not just the
    # number — the matrix gets revised.
    severity_label = getattr(sev, payload.consequence_type, None) if sev else None

    residual_rating = None
    if payload.residual_likelihood_code and payload.residual_severity_level is not None:
        residual_rating, _ = await _rate(
            db, payload.residual_likelihood_code, payload.residual_severity_level)

    row = RiskAssessment(
        id=uuid.uuid4(),
        source_type=payload.source_type, source_id=payload.source_id,
        hazard_id=payload.hazard_id, hazard_label=payload.hazard_label,
        description=payload.description,
        consequence_type=payload.consequence_type,
        likelihood_code=payload.likelihood_code, severity_level=payload.severity_level,
        rating=rating, likelihood_label=likelihood_label, severity_label=severity_label,
        controls=payload.controls,
        residual_likelihood_code=payload.residual_likelihood_code,
        residual_severity_level=payload.residual_severity_level,
        residual_rating=residual_rating,
        assessed_by=_user_id(user),
        assessed_by_name=await _resolve_name(db, _user_id(user)),
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def _may_see(db, user: dict, source_type: str | None, source_id: uuid.UUID | None) -> bool:
    """Whether this caller may read the assessments of that record.

    ★ An assessment belongs to the record it is about, so it follows that
    record's own rule rather than having one of its own. Reading an incident
    needs ehs.incident.read, or being the person who reported it or was
    involved — and an assessment of an incident states what could happen, how
    bad, and who judged it. Served on a looser permission it is the incident,
    read around the incident's gate. The third acceptance round found exactly
    this: the incident answered 403 and its assessments answered 200.

    A hazard or a job analysis is deliberately open to everybody. Both are
    plant-wide safety information a worker is meant to consult before starting,
    and their own registers are readable on the same permission.
    """
    if source_type == "incident" and source_id is not None:
        incident = await db.get(Incident, source_id)
        if incident is None:
            return True  # nothing exists to disclose; the empty list is honest
        return await _may_read(db, user, incident)
    return True


@router.get("/assessments", response_model=list[AssessmentOut])
async def list_assessments(
    db: SessionDep, user: CanReportIncident,
    source_type: str | None = None, source_id: uuid.UUID | None = None,
    limit: int = Query(default=100, le=300),
):
    # ★ Asking without naming a record is browsing every assessment on site,
    # incidents included. That is the register's permission, not the reporting
    # one — and a caller who omits the filter must not get what a caller who
    # names an incident would be refused.
    if source_id is None:
        if not await has_permission(db, _user_id(user), user.get("role", ""),
                                    "ehs.incident.read"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Say which record's assessments you want. Listing them across "
                "records needs permission to browse the incident register.")
    elif not await _may_see(db, user, source_type, source_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only see the assessments of incidents you reported or "
            "were involved in")

    q = select(RiskAssessment)
    if source_type:
        q = q.where(RiskAssessment.source_type == source_type)
    if source_id:
        q = q.where(RiskAssessment.source_id == source_id)
    return list((await db.execute(
        q.order_by(RiskAssessment.assessed_at.desc()).limit(limit))).scalars().all())
