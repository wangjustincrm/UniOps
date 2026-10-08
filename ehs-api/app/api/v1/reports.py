"""The reporting tab: one engine, many reports."""
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from uniops_authz import has_permission

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanReadIncident
from app.services import reporting

router = APIRouter(prefix="/reports")


class EntityOut(BaseModel):
    key: str
    label: str
    dimensions: list[str]
    # Whether a bar can be opened onto the records behind it.
    listable: bool = False


class DimensionOut(BaseModel):
    key: str
    label: str


class RecordRow(BaseModel):
    id: str
    ref: str | None
    title: str | None
    at: str | None
    path: str | None


class RecordsOut(BaseModel):
    total: int
    rows: list[RecordRow]


# Repeated `filter=dimension:value`. Nothing after the colon means the bar the
# engine labels "Not recorded". The dimension is checked against the entity's
# own list in the engine; the value only ever travels as a bound parameter.
FilterParam = Query(default=[], alias="filter")


def _filters(raw: list[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for item in raw:
        key, sep, value = item.partition(":")
        if not sep or not key:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"A filter is written dimension:value, not '{item}'")
        out[key] = value or None
    return out


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
    # Named rather than counted: "six months missing" is a number somebody can
    # act on only once they know which six.
    hours_months_missing: list[str] = []
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
    return [EntityOut(key=e.key, label=e.label, dimensions=list(e.dimensions),
                      listable=e.ref is not None)
            for e in reporting.ENTITIES]


@router.get("/dimensions", response_model=list[DimensionOut])
async def list_dimensions(user: CanReadIncident):  # noqa: ARG001
    """The names of the groupings, so the screen does not keep its own copy."""
    return [DimensionOut(key=d.key, label=d.label) for d in reporting.DIMENSIONS.values()]


@router.get("/group", response_model=list[GroupRow])
async def group(
    db: SessionDep, user: CanReadIncident,  # noqa: ARG001
    entity: str, dimension: str,
    date_from: date | None = None, date_to: date | None = None,
    filter: list[str] = FilterParam,  # noqa: A002
):
    try:
        return await reporting.group_by(
            db, entity=entity, dimension=dimension, date_from=date_from, date_to=date_to,
            filters=_filters(filter))
    except reporting.ReportError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


@router.get("/compare", response_model=list[CompareRow])
async def compare(
    db: SessionDep, user: CanReadIncident,  # noqa: ARG001
    entity: str, dimension: str,
    a_from: date, a_to: date, b_from: date, b_to: date,
    filter: list[str] = FilterParam,  # noqa: A002
):
    """The same grouping over two periods — this year against last."""
    try:
        return await reporting.compare_periods(
            db, entity=entity, dimension=dimension,
            period_a=(a_from, a_to), period_b=(b_from, b_to), filters=_filters(filter))
    except reporting.ReportError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


@router.get("/records", response_model=RecordsOut)
async def records(
    db: SessionDep, user: CanReadIncident,
    entity: str,
    date_from: date | None = None, date_to: date | None = None,
    filter: list[str] = FilterParam,  # noqa: A002
):
    """The records behind a bar. Round 5 feedback: searching, not just counting.

    ★ Held to the entity's own read key as well as this tab's. The counts are
    harmless; a list of names against courses is somebody's training file.
    """
    ent = reporting.BY_KEY.get(entity)
    if ent is not None and not await has_permission(
            db, _user_id(user), user.get("role", ""), ent.records_permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"Listing {ent.label.lower()} needs {ent.records_permission}")
    try:
        return await reporting.records(
            db, entity=entity, date_from=date_from, date_to=date_to,
            filters=_filters(filter))
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
