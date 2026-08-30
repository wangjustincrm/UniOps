"""Training records, certifications, and the compliance gap report."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.deps import SessionDep
from app.core.permissions import CanReadTraining, CanWriteTraining
from app.crud import training as crud
from app.schemas.training import (
    CertificationIn,
    CertificationOut,
    CourseOut,
    GapReport,
    TrainingRecordIn,
    TrainingRecordOut,
)

router = APIRouter()


def _user_id(payload: dict) -> uuid.UUID:
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token carries no usable subject") from None


def _cert_out(cert, today: date) -> CertificationOut:
    out = CertificationOut.model_validate(cert)
    if cert.expires_on:
        out.is_expired = cert.expires_on < today
        out.days_until_expiry = (cert.expires_on - today).days
    return out


class RenewIn(BaseModel):
    issued_on: date | None = None
    expires_on: date = Field(description="The new expiry — must be in the future")


@router.get("/courses", response_model=list[CourseOut])
async def list_courses(
    db: SessionDep,
    user: CanReadTraining,  # noqa: ARG001
    statutory_only: bool = False,
):
    return [CourseOut.model_validate(c) for c in
            await crud.list_courses(db, statutory_only=statutory_only)]


@router.get("/records", response_model=list[TrainingRecordOut])
async def list_records(
    db: SessionDep,
    user: CanReadTraining,  # noqa: ARG001
    user_id: uuid.UUID | None = None,
    course_id: uuid.UUID | None = None,
):
    return [TrainingRecordOut.model_validate(r) for r in
            await crud.list_training(db, user_id=user_id, course_id=course_id)]


@router.post("/records", response_model=TrainingRecordOut, status_code=status.HTTP_201_CREATED)
async def record_training(payload: TrainingRecordIn, db: SessionDep, user: CanWriteTraining):
    record = await crud.record_training(db, payload, recorded_by=_user_id(user))
    return TrainingRecordOut.model_validate(record)


@router.get("/certifications", response_model=list[CertificationOut])
async def list_certifications(
    db: SessionDep,
    user: CanReadTraining,  # noqa: ARG001
    user_id: uuid.UUID | None = None,
    expiring_within_days: int | None = Query(
        default=None, ge=0, le=365,
        description="Certifications expiring within this many days, expired ones included."),
):
    today = date.today()
    rows = await crud.list_certifications(
        db, user_id=user_id, expiring_within_days=expiring_within_days, today=today)
    return [_cert_out(c, today) for c in rows]


@router.post("/certifications", response_model=CertificationOut,
             status_code=status.HTTP_201_CREATED)
async def add_certification(payload: CertificationIn, db: SessionDep, user: CanWriteTraining):  # noqa: ARG001
    cert = await crud.add_certification(db, payload)
    return _cert_out(cert, date.today())


@router.post("/certifications/{cert_id}/renew", response_model=CertificationOut)
async def renew_certification(
    cert_id: uuid.UUID, payload: RenewIn, db: SessionDep, user: CanWriteTraining,  # noqa: ARG001
):
    """Push the expiry out and reset the clock that was chasing it."""
    cert = await crud.get_certification(db, cert_id)
    if cert.expires_on and payload.expires_on <= cert.expires_on:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The new expiry ({payload.expires_on}) must be later than the current "
            f"one ({cert.expires_on}) — renewing cannot shorten a certification",
        )
    await crud.renew_certification(
        db, cert, issued_on=payload.issued_on, expires_on=payload.expires_on)
    return _cert_out(cert, date.today())


@router.get("/gaps", response_model=GapReport)
async def training_gaps(db: SessionDep, user: CanReadTraining):  # noqa: ARG001
    """Who is missing which required training.

    Normally the first record a Ministry inspector asks for, so it is computed
    from the requirement itself — courses that apply to everyone plus those a
    person's position requires — rather than from a hand-maintained list.
    """
    return await crud.training_gaps(db)
