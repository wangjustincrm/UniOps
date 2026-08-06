"""GET /api/v1/net-requirement — monthly net requirement per material (Task 4).

Thin HTTP layer over `app/services/net_requirement.py`, which owns the
opening-stock definition and the pure monthly-rollforward math — see that
module's docstring for the formulas. This module's only jobs are: (1) load
the forecast version's month grid and per-material forecast (same pattern as
`app/api/v1/forecast.py`'s `GET .../grid`, duplicated locally rather than
importing that module's private `_load_lines_by_material` helper — it's a
handful of lines and keeps this endpoint decoupled from forecast.py's
internals), (2) fetch each material's opening-stock breakdown, (3) call
`compute_net_requirements`, (4) shape the response.

`material_code` omitted returns every material that has at least one
forecast line in this version (paginated); `material_code` given returns
that single material's breakdown, 404 if it has no forecast line in this
version (mirrors the rest of this service's "unknown ref -> 404" idiom,
e.g. `_get_version_or_404`).

Gated `mrp.report.view` — this is a read/report endpoint, same permission as
`GET /forecast/versions/{id}/grid` and `GET /inventory/lots`.

Each item's `name` is resolved via `app.services.mdm_client.resolve_material_names`
— one batched call per request (never per material), same degrade-on-failure
contract as the forecast grid/export and the consignment stock list (see
those modules' docstrings): mdm-api trouble means every item's `name` comes
back None, never a broken/5xx response. There is no frontend page consuming
this endpoint yet, but the response shape is fixed here up front for
consistency with every other endpoint that returns a bare `material_code` —
whichever page eventually renders this data won't hit the same "just a code,
no name" gap the forecast grid did.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.authz import require_permission
from app.core.deps import BearerToken, SessionDep
from app.models.forecast import ForecastLine, ForecastVersion
from app.services.mdm_client import resolve_material_names
from app.services.net_requirement import compute_net_requirements, get_opening_stock_breakdown

router = APIRouter(tags=["net-requirement"])

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]


# ── Schemas ──────────────────────────────────────────────────────────────


class NetRowResponse(BaseModel):
    month: str
    forecast_qty: Decimal
    opening_stock: Decimal
    net_requirement: Decimal
    closing_stock: Decimal


class StockSourcesResponse(BaseModel):
    wms_qty: Decimal
    consignment_qty: Decimal
    consignment_count_date: date | None
    wms_synced_at: datetime | None


class MaterialNetRequirementResponse(BaseModel):
    material_code: str
    name: str | None = None
    months: list[NetRowResponse]
    stock_sources: StockSourcesResponse


class NetRequirementListResponse(BaseModel):
    items: list[MaterialNetRequirementResponse]
    total: int
    page: int
    page_size: int


# ── Helpers ──────────────────────────────────────────────────────────────


def _generate_months(start_month: str, count: int) -> list[str]:
    """Mirrors app/api/v1/forecast.py::_generate_months exactly (a version's
    month list is always derived from horizon_start_month/horizon_months,
    never persisted separately) — duplicated locally per this module's
    docstring rather than importing forecast.py's private helper."""
    year, month = (int(p) for p in start_month.split("-"))
    months = []
    for i in range(count):
        m0 = month - 1 + i
        y = year + m0 // 12
        mm = m0 % 12 + 1
        months.append(f"{y:04d}-{mm:02d}")
    return months


async def _get_version_or_404(db: SessionDep, version_id: uuid.UUID) -> ForecastVersion:
    version = await db.get(ForecastVersion, version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="forecast version not found")
    return version


async def _load_forecast_by_material(
    db: SessionDep, version_id: uuid.UUID, months: list[str],
) -> dict[str, dict[str, Decimal]]:
    month_index = set(months)
    lines = (await db.execute(
        select(ForecastLine).where(ForecastLine.version_id == version_id)
    )).scalars().all()
    by_material: dict[str, dict[str, Decimal]] = {}
    for line in lines:
        if line.month not in month_index:
            continue  # outside the version's current horizon (e.g. after a horizon edit)
        by_material.setdefault(line.material_code, {})[line.month] = line.qty
    return by_material


async def _build_material_response(
    db: SessionDep, material_code: str, months: list[str], forecast_cells: dict[str, Decimal],
    name: str | None,
) -> MaterialNetRequirementResponse:
    forecast_by_month = {m: forecast_cells.get(m, Decimal("0")) for m in months}
    breakdown = await get_opening_stock_breakdown(db, material_code)
    rows = compute_net_requirements(forecast_by_month, breakdown.opening_stock)
    return MaterialNetRequirementResponse(
        material_code=material_code,
        name=name,
        months=[
            NetRowResponse(
                month=r.month, forecast_qty=r.forecast_qty, opening_stock=r.opening_stock,
                net_requirement=r.net_requirement, closing_stock=r.closing_stock,
            )
            for r in rows
        ],
        stock_sources=StockSourcesResponse(
            wms_qty=breakdown.wms_qty,
            consignment_qty=breakdown.consignment_qty,
            consignment_count_date=breakdown.consignment_count_date,
            wms_synced_at=breakdown.wms_synced_at,
        ),
    )


# ── Endpoint ─────────────────────────────────────────────────────────────


@router.get("/net-requirement", response_model=NetRequirementListResponse)
async def get_net_requirement(
    db: SessionDep,
    _: ReadDep,
    token: BearerToken,
    version_id: uuid.UUID = Query(...),
    material_code: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    version = await _get_version_or_404(db, version_id)
    months = _generate_months(version.horizon_start_month, version.horizon_months)
    by_material = await _load_forecast_by_material(db, version_id, months)

    if material_code is not None:
        if material_code not in by_material:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"material_code {material_code!r} has no forecast line in version {version_id}",
            )
        names = await resolve_material_names(token)
        item = await _build_material_response(
            db, material_code, months, by_material[material_code], names.get(material_code),
        )
        return NetRequirementListResponse(items=[item], total=1, page=1, page_size=1)

    all_codes = sorted(by_material)
    total = len(all_codes)
    page_codes = all_codes[(page - 1) * page_size: (page - 1) * page_size + page_size]
    # One batched mdm-api round trip for this page (never per material) —
    # skipped entirely when the page is empty. Never raises: see
    # resolve_material_names' docstring — mdm-api trouble degrades every
    # item's name to None rather than breaking this read.
    names = await resolve_material_names(token) if page_codes else {}
    items = [
        await _build_material_response(db, code, months, by_material[code], names.get(code))
        for code in page_codes
    ]
    return NetRequirementListResponse(items=items, total=total, page=page, page_size=page_size)
