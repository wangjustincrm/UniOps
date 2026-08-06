"""HTTP layer over `app/services/demand_series.py` (Continuous Sales Forecast
redesign, plan doc docs/superpowers/plans/2026-08-06-continuous-sales-forecast.md,
Task 3). This module owns none of the write/read logic itself — it just
shapes requests/responses around `read_series_grid`/`upsert_cells` and maps
that service's two named exceptions onto HTTP.

- `GET /series?from=YYYY-MM&to=YYYY-MM` — same `GridResponse` shape
  `app/api/v1/forecast.py`'s `GET .../grid` returns, sourced from the
  continuous `mrp_demand_series` table instead of a version's lines.
  Forwards the caller's bearer token to `resolve_material_names` (inside
  `read_series_grid`) the same way `forecast.py`/`consignment.py` do —
  see `read_series_grid`'s docstring for the never-raises/degrade-to-None
  contract on `name`.

- `PUT /series/cells` — the one write path. `current_month` is resolved
  HERE, server-side, from `datetime.now(timezone.utc)`, and passed
  explicitly into `upsert_cells` — never left to that function's own
  wall-clock default (Task 2 review flagged relying on the default as a
  footgun: a test or a future caller that forgets to pass `current_month`
  would silently get "now" instead of an intentional value). `PastMonthError`/
  `UomError` — validated by `upsert_cells` over the whole batch before
  anything is written — become a 422 with a readable detail; nothing from a
  rejected batch is written (that atomicity is `upsert_cells`'s guarantee,
  not this module's).

- `GET /series/change-log?material_code=&month=` — raw
  `mrp_forecast_change_log` rows for one material, newest first. `month` is
  optional: omitted returns every month's history for that material.

- `POST /series/outlook` — "Generate Outlook" (Task 4): freezes
  `[anchor_month, anchor_month + horizon_months)` of the living series into
  a new immutable `mrp_forecast_versions` snapshot via
  `app/services/demand_series.py::freeze_outlook`, returned in the same
  `ForecastVersionResponse` shape `forecast.py`'s `POST /versions` returns
  (reused as-is, not redefined here). Does not supersede any other
  version — see `freeze_outlook`'s docstring and `forecast.py`'s `/confirm`
  handler, which lost that behaviour in this same task.

Read endpoints are gated `mrp.report.view`, the write endpoints
`mrp.demand.write` — same two keys `forecast.py`/`consignment.py` use.
"""
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from app.api.v1.forecast import ForecastVersionResponse
from app.core.authz import require_permission
from app.core.deps import BearerToken, SessionDep
from app.models.demand_series import MrpForecastChangeLog
from app.services.demand_series import (
    CellChange,
    PastMonthError,
    UomError,
    freeze_outlook,
    read_series_grid,
    upsert_cells,
)

router = APIRouter(prefix="/series", tags=["series"])

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]
WriteDep = Annotated[dict, Depends(require_permission("mrp.demand.write"))]

# Same 'YYYY-MM' shape forecast.py's ForecastVersionCreate._valid_month
# validates on the body side — here it guards the from/to/month QUERY
# params so a malformed value 422s via FastAPI's own validation instead of
# reaching generate_month_range()'s int(part) parsing as an unhandled
# ValueError -> 500.
_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


# ── Schemas ──────────────────────────────────────────────────────────────


class SeriesGridRow(BaseModel):
    material_code: str
    name: str | None = None
    cells: dict[str, Decimal]
    total: Decimal


class SeriesGridResponse(BaseModel):
    months: list[str]
    rows: list[SeriesGridRow]
    column_totals: dict[str, Decimal]
    grand_total: Decimal


class SeriesCellUpsert(BaseModel):
    material_code: str
    month: str
    qty: Decimal
    uom: str = "KG"

    @field_validator("month")
    @classmethod
    def _valid_month(cls, v: str) -> str:
        # GET's from/to/month query params validate against _MONTH_PATTERN
        # via FastAPI's own `pattern=` kwarg (see that constant's docstring
        # above); this is the same guard on the PUT body's `month`, which
        # FastAPI's Query(pattern=...) can't reach. Without it, a malformed
        # value like "2026-1" sails past upsert_cells' `cell.month <
        # current_month` STRING comparison (at char index 6, '1' > '0'
        # compares greater than "2026-08", so it reads as "not past" even
        # though it's not a valid month at all) and gets written straight
        # into a real historical row — defeating the past-read-only
        # invariant. A too-long value like "2026-011" would also overflow
        # the mrp_demand_series.month CHAR(7) column as an unhandled 500.
        if not re.match(_MONTH_PATTERN, v):
            raise ValueError("month must be 'YYYY-MM'")
        return v


class SeriesCellsUpsertRequest(BaseModel):
    cells: list[SeriesCellUpsert]


class SeriesCellsUpsertResponse(BaseModel):
    upserted: int
    changed: int


class ChangeLogItem(BaseModel):
    material_code: str
    month: str
    old_qty: Decimal | None
    new_qty: Decimal | None
    source: str
    changed_by: uuid.UUID | None
    changed_at: datetime

    model_config = {"from_attributes": True}


class ChangeLogResponse(BaseModel):
    items: list[ChangeLogItem]


class OutlookFreezeRequest(BaseModel):
    anchor_month: str
    horizon_months: int = 18

    @field_validator("anchor_month")
    @classmethod
    def _valid_anchor_month(cls, v: str) -> str:
        if not re.match(_MONTH_PATTERN, v):
            raise ValueError("anchor_month must be 'YYYY-MM'")
        return v

    @field_validator("horizon_months")
    @classmethod
    def _positive_horizon(cls, v: int) -> int:
        if v < 1:
            raise ValueError("horizon_months must be >= 1")
        return v


# ── Helpers ──────────────────────────────────────────────────────────────


def _sub_to_uuid(payload: dict) -> uuid.UUID | None:
    """Same idiom `consignment.py`/`forecast.py` use to turn the JWT `sub`
    claim into `changed_by` — never raises on a malformed/missing sub, just
    falls back to an unattributed (None) change."""
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return uuid.UUID(sub)
    except ValueError:
        return None


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("", response_model=SeriesGridResponse)
async def get_series(
    db: SessionDep,
    _: ReadDep,
    token: BearerToken,
    from_month: str = Query(..., alias="from", pattern=_MONTH_PATTERN),
    to_month: str = Query(..., alias="to", pattern=_MONTH_PATTERN),
):
    return await read_series_grid(db, from_month, to_month, token=token)


@router.put("/cells", response_model=SeriesCellsUpsertResponse)
async def put_series_cells(body: SeriesCellsUpsertRequest, db: SessionDep, payload: WriteDep):
    # Resolved here, not left to upsert_cells' own datetime.now() default —
    # see this module's docstring for why (Task 2 review carry-over).
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    changed_by = _sub_to_uuid(payload)
    cells = [
        CellChange(material_code=c.material_code, month=c.month, qty=c.qty, uom=c.uom)
        for c in body.cells
    ]
    try:
        result = await upsert_cells(
            db, cells, current_month=current_month, changed_by=changed_by, source="manual",
        )
    except (PastMonthError, UomError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"upserted": result.upserted, "changed": result.changed}


@router.get("/change-log", response_model=ChangeLogResponse)
async def get_change_log(
    db: SessionDep,
    _: ReadDep,
    material_code: str = Query(...),
    month: str | None = Query(default=None, pattern=_MONTH_PATTERN),
):
    stmt = select(MrpForecastChangeLog).where(MrpForecastChangeLog.material_code == material_code)
    if month is not None:
        stmt = stmt.where(MrpForecastChangeLog.month == month)
    stmt = stmt.order_by(MrpForecastChangeLog.changed_at.desc())
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": rows}


@router.post("/outlook", response_model=ForecastVersionResponse, status_code=status.HTTP_201_CREATED)
async def post_outlook(body: OutlookFreezeRequest, db: SessionDep, payload: WriteDep):
    created_by = _sub_to_uuid(payload)
    return await freeze_outlook(
        db, body.anchor_month, body.horizon_months, created_by=created_by,
    )
