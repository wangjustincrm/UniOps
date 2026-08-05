"""Sales forecast versions + grid cell API (Phase 1A Task 1).

`POST /versions` builds the version's 18 (default) consecutive `YYYY-MM`
months itself from `horizon_start_month` — a client-supplied month list is
never trusted/accepted anywhere in this module; `_generate_months()` is the
single source of that sequence and both `POST /versions` (validation only)
and `GET /versions/{id}/grid` (rendering) call it fresh from the persisted
`horizon_start_month`/`horizon_months` columns.

`copy_from_version_id` copies the source version's lines **as-is by
(material_code, month)** into the new version — there is no month-shifting.
A source line whose month falls outside the new version's generated horizon
is simply dropped (not remapped to an equivalent relative month). This
keeps "copy from a similar past version" simple and unsurprising: if you
start the new sheet at a different month, only the overlapping months carry
forward.

`GET /versions/{id}/grid` returns `name: null` for every row — material
names live in mdm-api and this task does not wire a cross-service lookup
(no MdmClient-style client exists yet in this service, see app/core/config.py
— out of scope here per the task brief; the frontend can resolve
material_code -> name against mdm-api's own materials list).

Write endpoints (`POST /versions`, `PUT .../cells`, `POST .../confirm`) are
gated `mrp.demand.write`; reads (`GET /versions`, `GET .../grid`) are gated
`mrp.report.view`. A version whose status is not 'draft' rejects writes to
its cells (409) — 'confirmed'/'superseded' versions are immutable.
"""
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

import anyio
import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select

from app.core.authz import require_permission
from app.core.deps import BearerToken, SessionDep
from app.models.forecast import ForecastLine, ForecastVersion
from app.services import forecast_io

router = APIRouter(prefix="/forecast", tags=["forecast"])

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]
WriteDep = Annotated[dict, Depends(require_permission("mrp.demand.write"))]

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


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


class ForecastVersionCreate(BaseModel):
    horizon_start_month: str
    horizon_months: int = 18
    note: str | None = None
    copy_from_version_id: uuid.UUID | None = None

    @field_validator("horizon_start_month")
    @classmethod
    def _valid_month(cls, v: str) -> str:
        if not _MONTH_RE.match(v):
            raise ValueError("horizon_start_month must be 'YYYY-MM'")
        return v

    @field_validator("horizon_months")
    @classmethod
    def _positive_horizon(cls, v: int) -> int:
        if v < 1:
            raise ValueError("horizon_months must be >= 1")
        return v


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


class CellUpsert(BaseModel):
    material_code: str
    month: str
    qty: Decimal


class CellsUpsertRequest(BaseModel):
    cells: list[CellUpsert]


class SkippedCell(BaseModel):
    material_code: str
    month: str


class CellsUpsertResponse(BaseModel):
    upserted: int
    skipped_frozen: list[SkippedCell]


class ImportRowError(BaseModel):
    row: int  # 1-based over data rows (header excluded); 0 = header-level error
    column: str
    reason: str


class ImportResponse(BaseModel):
    ok_rows: int
    error_rows: list[ImportRowError]
    skipped_frozen: list[SkippedCell]
    would_upsert: int


# ── Helpers ──────────────────────────────────────────────────────────────


async def _get_version_or_404(db: SessionDep, version_id: uuid.UUID) -> ForecastVersion:
    version = await db.get(ForecastVersion, version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="forecast version not found")
    return version


def _require_draft(version: ForecastVersion) -> None:
    if version.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"forecast version {version.id} is '{version.status}', not 'draft' — it is immutable",
        )


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


@router.post("/versions", response_model=ForecastVersionResponse, status_code=status.HTTP_201_CREATED)
async def create_version(body: ForecastVersionCreate, db: SessionDep, payload: WriteDep):
    created_by = None
    sub = payload.get("sub")
    if sub:
        try:
            created_by = uuid.UUID(sub)
        except ValueError:
            created_by = None

    version = ForecastVersion(
        version_no=f"FCV-{body.horizon_start_month}-{uuid.uuid4().hex[:8].upper()}",
        status="draft",
        horizon_start_month=body.horizon_start_month,
        horizon_months=body.horizon_months,
        note=body.note,
        created_by=created_by,
    )
    db.add(version)
    await db.flush()  # assign version.id for the FK below

    if body.copy_from_version_id is not None:
        source = await db.get(ForecastVersion, body.copy_from_version_id)
        if source is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="copy_from_version_id not found")
        target_months = set(_generate_months(body.horizon_start_month, body.horizon_months))
        src_lines = (await db.execute(
            select(ForecastLine).where(ForecastLine.version_id == source.id)
        )).scalars().all()
        for line in src_lines:
            if line.month not in target_months:
                continue  # as-is copy by (material, month); no shifting — see module docstring
            db.add(ForecastLine(
                version_id=version.id,
                material_code=line.material_code,
                month=line.month,
                qty=line.qty,
                uom=line.uom,
                freeze_flag=False,  # a fresh draft copy starts unfrozen
            ))

    await db.commit()
    await db.refresh(version)
    return version


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
async def get_grid(version_id: uuid.UUID, db: SessionDep, _: ReadDep):
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

    column_totals: dict[str, Decimal] = {m: Decimal("0") for m in months}
    rows: list[dict] = []
    grand_total = Decimal("0")
    for material_code in sorted(by_material):
        material_cells = by_material[material_code]
        cells = {m: material_cells.get(m, Decimal("0")) for m in months}
        total = sum(cells.values(), Decimal("0"))
        rows.append({"material_code": material_code, "name": None, "cells": cells, "total": total})
        for m in months:
            column_totals[m] += cells[m]
        grand_total += total

    return {
        "months": months,
        "rows": rows,
        "column_totals": column_totals,
        "grand_total": grand_total,
    }


@router.put("/versions/{version_id}/cells", response_model=CellsUpsertResponse)
async def upsert_cells(version_id: uuid.UUID, body: CellsUpsertRequest, db: SessionDep, _: WriteDep):
    version = await _get_version_or_404(db, version_id)
    _require_draft(version)

    if not body.cells:
        return {"upserted": 0, "skipped_frozen": []}

    material_codes = {c.material_code for c in body.cells}
    existing_rows = (await db.execute(
        select(ForecastLine).where(
            ForecastLine.version_id == version_id,
            ForecastLine.material_code.in_(material_codes),
        )
    )).scalars().all()
    existing_by_key = {(r.material_code, r.month): r for r in existing_rows}

    upserted = 0
    skipped_frozen: list[dict] = []
    for cell in body.cells:
        key = (cell.material_code, cell.month)
        existing = existing_by_key.get(key)
        if existing is not None and existing.freeze_flag:
            skipped_frozen.append({"material_code": cell.material_code, "month": cell.month})
            continue
        if existing is not None:
            existing.qty = cell.qty
        else:
            new_line = ForecastLine(
                version_id=version_id,
                material_code=cell.material_code,
                month=cell.month,
                qty=cell.qty,
            )
            db.add(new_line)
            existing_by_key[key] = new_line  # dedupe repeated cells in the same request
        upserted += 1

    await db.commit()  # single transaction for the whole bulk upsert
    return {"upserted": upserted, "skipped_frozen": skipped_frozen}


@router.post("/versions/{version_id}/confirm", response_model=ForecastVersionResponse)
async def confirm_version(version_id: uuid.UUID, db: SessionDep, _: WriteDep):
    version = await _get_version_or_404(db, version_id)
    _require_draft(version)

    # Only one version is 'confirmed' system-wide at a time — confirming
    # this one supersedes whichever version currently holds that status.
    await db.execute(
        sa.update(ForecastVersion)
        .where(ForecastVersion.status == "confirmed")
        .values(status="superseded")
    )

    version.status = "confirmed"
    version.confirmed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(version)
    return version


# ── Excel template / import / export (Task 2) ──────────────────────────────


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
async def export_grid(version_id: uuid.UUID, db: SessionDep, _: ReadDep):
    """Export the current grid as xlsx, in the same shape as the template."""
    version = await _get_version_or_404(db, version_id)
    months = _generate_months(version.horizon_start_month, version.horizon_months)
    by_material = await _load_lines_by_material(db, version_id, months)
    rows = [
        {"material_code": code, "name": None, "cells": cells}
        for code, cells in sorted(by_material.items())
    ]
    content = forecast_io.build_export_workbook(months, rows)
    filename = f"forecast-export-{version.version_no}.xlsx"
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/versions/{version_id}/import", response_model=ImportResponse)
async def import_forecast(
    version_id: uuid.UUID,
    db: SessionDep,
    _: WriteDep,
    token: BearerToken,
    file: Annotated[UploadFile, File()],
    dry_run: bool = Query(default=True),
):
    """Validate (and, unless dry_run, apply) an uploaded forecast xlsx.

    `dry_run=true` runs every check — including a read-only frozen-cell
    lookup so the preview accurately reports `skipped_frozen`/`would_upsert`
    — but writes nothing; the frontend's "preview validation report" depends
    on this. `dry_run=false` applies via the same upsert semantics as
    PUT .../cells (frozen cells skipped and reported, never overwritten).
    """
    version = await _get_version_or_404(db, version_id)
    _require_draft(version)
    months = _generate_months(version.horizon_start_month, version.horizon_months)

    raw = await file.read()
    # mdm-api material codes are fetched once per import (never per row),
    # via a blocking httpx.Client bridged onto a worker thread.
    valid_codes = await anyio.to_thread.run_sync(forecast_io.fetch_valid_material_codes, token)
    ok_cells, error_rows = forecast_io.parse_import_workbook(raw, months, valid_codes)

    skipped_frozen: list[dict] = []
    if ok_cells:
        material_codes = {c["material_code"] for c in ok_cells}
        existing_rows = (await db.execute(
            select(ForecastLine).where(
                ForecastLine.version_id == version_id,
                ForecastLine.material_code.in_(material_codes),
            )
        )).scalars().all()
        existing_by_key = {(r.material_code, r.month): r for r in existing_rows}

        for cell in ok_cells:
            key = (cell["material_code"], cell["month"])
            existing = existing_by_key.get(key)
            if existing is not None and existing.freeze_flag:
                skipped_frozen.append({"material_code": cell["material_code"], "month": cell["month"]})
                continue
            if not dry_run:
                if existing is not None:
                    existing.qty = cell["qty"]
                else:
                    new_line = ForecastLine(
                        version_id=version_id,
                        material_code=cell["material_code"],
                        month=cell["month"],
                        qty=cell["qty"],
                    )
                    db.add(new_line)
                    existing_by_key[key] = new_line  # dedupe repeated cells in the same file

        if not dry_run:
            await db.commit()  # single transaction for the whole import

    return {
        "ok_rows": len(ok_cells),
        "error_rows": error_rows,
        "skipped_frozen": skipped_frozen,
        "would_upsert": len(ok_cells) - len(skipped_frozen),
    }
