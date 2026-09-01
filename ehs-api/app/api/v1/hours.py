"""Monthly hours worked — the divisor for every injury rate.

Read by anyone who may see the dashboard, written by whoever manages Settings.
HSE asked for it to be editable after the fact, so a PUT on a month that
already has a figure replaces it rather than being refused: payroll
corrections happen, and a number that could only be entered once would leave a
rate wrong forever with no way to say so.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanManageSettings, CanReadIncident
from app.crud.incident import _resolve_name
from app.models.hours import HoursWorked
from app.services import hours as svc

router = APIRouter(prefix="/hours-worked")


class HoursIn(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    hours: Decimal = Field(ge=0, le=10_000_000)
    note: str | None = Field(default=None, max_length=200)


class HoursOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    year: int
    month: int
    hours: Decimal
    note: str | None
    entered_by_name: str | None
    entered_at: datetime | None


class YearOut(BaseModel):
    year: int
    months: list[HoursOut]
    total: Decimal
    months_found: int
    months_expected: int
    missing: list[str]
    note: str | None


@router.get("", response_model=YearOut)
async def get_year(
    db: SessionDep, user: CanReadIncident,  # noqa: ARG001
    year: int = Query(default=None, ge=2000, le=2100),
):
    """One year's figures, with whichever months have not been entered."""
    year = year or date.today().year
    rows = list((await db.execute(
        select(HoursWorked).where(HoursWorked.year == year)
        .order_by(HoursWorked.month))).scalars())
    total = await svc.hours_for_year(db, year)
    return YearOut(
        year=year,
        months=[HoursOut.model_validate(r) for r in rows],
        total=total.total,
        months_found=total.months_found,
        months_expected=total.months_expected,
        missing=total.missing,
        note=total.note(),
    )


@router.put("", response_model=HoursOut, status_code=status.HTTP_200_OK)
async def put_month(payload: HoursIn, db: SessionDep, user: CanManageSettings):
    """Set or correct one month's total."""
    existing = (await db.execute(
        select(HoursWorked).where(
            HoursWorked.year == payload.year, HoursWorked.month == payload.month,
        ))).scalar_one_or_none()

    user_id = _user_id(user)
    if existing is None:
        existing = HoursWorked(id=uuid.uuid4(), year=payload.year, month=payload.month)
        db.add(existing)

    existing.hours = payload.hours
    existing.note = payload.note
    existing.entered_by = user_id
    existing.entered_by_name = await _resolve_name(db, user_id)
    existing.entered_at = datetime.now(timezone.utc)
    await db.flush()
    return HoursOut.model_validate(existing)
