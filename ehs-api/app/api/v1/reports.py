"""The reporting tab: one engine, many reports."""
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.core.deps import SessionDep
from app.core.permissions import CanReadIncident
from app.services import reporting

router = APIRouter(prefix="/reports")


class EntityOut(BaseModel):
    key: str
    label: str
    dimensions: list[str]


class GroupRow(BaseModel):
    value: str | None
    label: str | None
    count: int


class CompareRow(BaseModel):
    value: str | None
    label: str | None
    period_a: int
    period_b: int
    change: int
    change_percent: int | None


class RatesOut(BaseModel):
    recordable: int
    lost_time: int
    dart_cases: int
    hours_worked: float | None
    trir: float | None
    lost_time_rate: float | None
    dart_rate: float | None
    note: str | None


@router.get("/entities", response_model=list[EntityOut])
async def list_entities(user: CanReadIncident):  # noqa: ARG001
    """What can be reported on, and how each can be grouped.

    Served so a report builder offers only combinations the engine can answer,
    instead of letting someone assemble one that 422s.
    """
    return [EntityOut(key=e.key, label=e.label, dimensions=list(e.dimensions))
            for e in reporting.ENTITIES]


@router.get("/group", response_model=list[GroupRow])
async def group(
    db: SessionDep, user: CanReadIncident,  # noqa: ARG001
    entity: str, dimension: str,
    date_from: date | None = None, date_to: date | None = None,
):
    try:
        return await reporting.group_by(
            db, entity=entity, dimension=dimension, date_from=date_from, date_to=date_to)
    except reporting.ReportError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


@router.get("/compare", response_model=list[CompareRow])
async def compare(
    db: SessionDep, user: CanReadIncident,  # noqa: ARG001
    entity: str, dimension: str,
    a_from: date, a_to: date, b_from: date, b_to: date,
):
    """The same grouping over two periods — this year against last."""
    try:
        return await reporting.compare_periods(
            db, entity=entity, dimension=dimension,
            period_a=(a_from, a_to), period_b=(b_from, b_to))
    except reporting.ReportError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


@router.get("/injury-rates", response_model=RatesOut)
async def injury_rates(
    db: SessionDep, user: CanReadIncident,  # noqa: ARG001
    hours_worked: float | None = Query(default=None, ge=0),
    date_from: date | None = None, date_to: date | None = None,
):
    """TRIR, lost-time and DART rates. Hours worked come from payroll."""
    return await reporting.injury_rates(
        db, hours_worked=hours_worked, date_from=date_from, date_to=date_to)
