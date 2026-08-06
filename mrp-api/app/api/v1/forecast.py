"""Sales forecast versions — read/export API (Phase 1A Task 1; write/draft
endpoints retired in the Continuous Sales Forecast redesign, Task 8).

A `ForecastVersion` is now an immutable snapshot: hand-built drafting
(`POST /versions`), cell editing (`PUT .../cells`), manual confirmation
(`POST .../confirm`) and file import (`POST .../import`) all moved to the
continuous series (`app/api/v1/series.py` edits `mrp_demand_series` directly;
`app/services/demand_series.py::freeze_outlook`, reached via
`POST /series/outlook`, is the only way a new version is created now — see
that module's docstring). This module keeps only the read/export surface: a
frozen outlook snapshot is still viewable/exportable here, and
`app/api/v1/mps.py` still reads versions as MPS input.

`_generate_months()` is the single source of a version's `YYYY-MM` column
sequence — always derived fresh from the persisted
`horizon_start_month`/`horizon_months` columns, never persisted as its own
list.

`GET /versions/{id}/grid` and `GET /versions/{id}/export` resolve each row's
`name` via `app.services.mdm_client.resolve_material_names` — one batched
call per request (never per row), forwarding the caller's bearer token. That
helper never raises: if mdm-api is unreachable/slow/erroring, both endpoints
degrade to `name: None` for every row (today's pre-lookup behavior) rather
than 5xx-ing or hanging — the grid/export's actual numbers never depend on
mdm-api, so a name-lookup failure must never take the whole read down.

All endpoints here are reads, gated `mrp.report.view`.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.authz import require_permission
from app.core.deps import BearerToken, SessionDep
from app.models.forecast import ForecastLine, ForecastVersion
from app.services import forecast_io
from app.services.mdm_client import resolve_material_names

router = APIRouter(prefix="/forecast", tags=["forecast"])

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]


def _generate_months(start_month: str, count: int) -> list[str]:
    """The single source of truth for a version's YYYY-MM column list —
    always derived from horizon_start_month/horizon_months, never accepted
    from a request body."""
    year, month = (int(p) for p in start_month.split("-"))
    months = []
    for i in range(count):
        m0 = month - 1 + i
        y = year + m0 // 12
        mm = m0 % 12 + 1
        months.append(f"{y:04d}-{mm:02d}")
    return months


# ── Schemas ──────────────────────────────────────────────────────────────


class ForecastVersionResponse(BaseModel):
    id: uuid.UUID
    version_no: str
    status: str
    horizon_start_month: str
    horizon_months: int
    note: str | None
    created_by: uuid.UUID | None
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    source_anchor_month: str | None = None

    model_config = {"from_attributes": True}


class ForecastVersionListResponse(BaseModel):
    items: list[ForecastVersionResponse]
    total: int
    page: int
    page_size: int


class GridRow(BaseModel):
    material_code: str
    name: str | None = None
    cells: dict[str, Decimal]
    total: Decimal


class GridResponse(BaseModel):
    months: list[str]
    rows: list[GridRow]
    column_totals: dict[str, Decimal]
    grand_total: Decimal


# ── Helpers ──────────────────────────────────────────────────────────────


async def _get_version_or_404(db: SessionDep, version_id: uuid.UUID) -> ForecastVersion:
    version = await db.get(ForecastVersion, version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="forecast version not found")
    return version


async def _load_lines_by_material(
    db: SessionDep, version_id: uuid.UUID, months: list[str],
) -> dict[str, dict[str, Decimal]]:
    """Shared by GET .../grid and GET .../export: current lines grouped by
    material_code -> {month: qty}, restricted to the version's current
    horizon (a line outside it — e.g. after a horizon edit — doesn't render)."""
    month_index = set(months)
    lines = (await db.execute(
        select(ForecastLine).where(ForecastLine.version_id == version_id)
    )).scalars().all()
    by_material: dict[str, dict[str, Decimal]] = {}
    for line in lines:
        if line.month not in month_index:
            continue
        by_material.setdefault(line.material_code, {})[line.month] = line.qty
    return by_material


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/versions", response_model=ForecastVersionListResponse)
async def list_versions(
    db: SessionDep,
    _: ReadDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    count_stmt = select(func.count()).select_from(ForecastVersion)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = select(ForecastVersion).order_by(ForecastVersion.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/versions/{version_id}/grid", response_model=GridResponse)
async def get_grid(version_id: uuid.UUID, db: SessionDep, _: ReadDep, token: BearerToken):
    version = await _get_version_or_404(db, version_id)
    months = _generate_months(version.horizon_start_month, version.horizon_months)
    month_index = set(months)

    lines = (await db.execute(
        select(ForecastLine).where(ForecastLine.version_id == version_id)
    )).scalars().all()

    by_material: dict[str, dict[str, Decimal]] = {}
    for line in lines:
        if line.month not in month_index:
            continue  # lines outside the current horizon (e.g. after a horizon edit) don't render
        by_material.setdefault(line.material_code, {})[line.month] = line.qty

    # One batched mdm-api round trip for the whole grid (never per row) —
    # skipped entirely when there are no rows to name. Never raises: see
    # resolve_material_names' docstring — mdm-api trouble degrades every
    # row's name to None rather than breaking this read.
    names = await resolve_material_names(token) if by_material else {}

    column_totals: dict[str, Decimal] = {m: Decimal("0") for m in months}
    rows: list[dict] = []
    grand_total = Decimal("0")
    for material_code in sorted(by_material):
        material_cells = by_material[material_code]
        cells = {m: material_cells.get(m, Decimal("0")) for m in months}
        total = sum(cells.values(), Decimal("0"))
        rows.append({
            "material_code": material_code,
            "name": names.get(material_code),
            "cells": cells,
            "total": total,
        })
        for m in months:
            column_totals[m] += cells[m]
        grand_total += total

    return {
        "months": months,
        "rows": rows,
        "column_totals": column_totals,
        "grand_total": grand_total,
    }


# ── Excel template / export (Task 2) ────────────────────────────────────────


@router.get("/versions/{version_id}/template")
async def download_template(version_id: uuid.UUID, db: SessionDep, _: ReadDep):
    """Empty xlsx: Material Code | Name | <the version's 18 YYYY-MM months>."""
    version = await _get_version_or_404(db, version_id)
    months = _generate_months(version.horizon_start_month, version.horizon_months)
    content = forecast_io.build_template_workbook(months)
    filename = f"forecast-template-{version.version_no}.xlsx"
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/versions/{version_id}/export")
async def export_grid(version_id: uuid.UUID, db: SessionDep, _: ReadDep, token: BearerToken):
    """Export the current grid as xlsx, in the same shape as the template."""
    version = await _get_version_or_404(db, version_id)
    months = _generate_months(version.horizon_start_month, version.horizon_months)
    by_material = await _load_lines_by_material(db, version_id, months)
    # Same one-batched-call-per-request/degrade-on-failure contract as
    # GET .../grid — see resolve_material_names' docstring.
    names = await resolve_material_names(token) if by_material else {}
    rows = [
        {"material_code": code, "name": names.get(code), "cells": cells}
        for code, cells in sorted(by_material.items())
    ]
    content = forecast_io.build_export_workbook(months, rows)
    filename = f"forecast-export-{version.version_no}.xlsx"
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


