"""Canonical BOM endpoints (MRP phase0 task 5).

GET  /boms/effective — single effective BOM (header + nested lines +
     substitutes) for a product code as of a date.
POST /boms/sync      — refresh nc_bom mirror (Task 4) then transform into
     boms/bom_lines/bom_substitutes (Task 5).

Reads: any authenticated role (materials.py/uom_conversions.py idiom).
Writes (sync): gated require_permission("data_maintenance"), same as
materials.py's POST /materials/sync.

Version-selection rule for /effective (survey recommendation, since
multiple approved HVERSIONs can coexist per product — survey §8, e.g.
CS0026 has 7 approved versions 1.0-1.6): among APPROVED boms for the
product, walk versions from highest to lowest (numeric HVERSION ordering,
e.g. '1.10' > '1.9'); the first version that has at least one line whose
CBEGINPERIOD/CENDPERIOD-derived effective window covers the query date
wins. Only that version's date-covering lines are returned — a bom's other,
not-yet/no-longer-effective lines are excluded from the response.
"""
import uuid
from datetime import date as date_type
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.bom import Bom, BomLine
from app.services.nc_bom_sync.canonical_sync import sync_boms

router = APIRouter(prefix="/boms", tags=["boms"])

SyncDep = Annotated[dict, Depends(require_permission("data_maintenance"))]


class BomSubstituteResponse(BaseModel):
    id: uuid.UUID
    substitute_material_code: str
    priority: int
    mode: str

    model_config = {"from_attributes": True}


class BomLineResponse(BaseModel):
    id: uuid.UUID
    line_no: int
    component_material_code: str
    qty_per: Decimal
    uom: str | None
    scrap_rate: Decimal
    effective_from: date_type | None
    effective_to: date_type | None
    substitutes: list[BomSubstituteResponse] = []

    model_config = {"from_attributes": True}


class BomEffectiveResponse(BaseModel):
    id: uuid.UUID
    product_material_code: str
    bom_type: str | None
    version: str | None
    factory_code: str | None
    status: str
    yield_rate: Decimal
    nc_source_pk: str
    lines: list[BomLineResponse]

    model_config = {"from_attributes": True}


class BomSyncResponse(BaseModel):
    boms: int
    lines: int
    substitutes: int
    skipped: int
    warnings: int


def _version_key(version: str | None) -> tuple:
    """Numeric HVERSION ordering: '1.10' > '1.9' > '1.0'. Falls back to
    (0,) for blank/unparsable versions so they sort lowest, never crash."""
    if not version:
        return (0,)
    parts: list[int] = []
    for segment in str(version).split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _covers(line: BomLine, on_date: date_type) -> bool:
    if line.effective_from is not None and line.effective_from > on_date:
        return False
    if line.effective_to is not None and line.effective_to < on_date:
        return False
    return True


@router.get("/effective", response_model=BomEffectiveResponse)
async def get_effective_bom(
    product: str = Query(..., description="product_material_code"),
    date: date_type = Query(..., description="query date, YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    candidates = list((await db.execute(
        select(Bom).where(Bom.product_material_code == product, Bom.status == "approved")
    )).scalars().all())
    candidates.sort(key=lambda b: _version_key(b.version), reverse=True)

    for bom in candidates:
        all_lines = list((await db.execute(
            select(BomLine)
            .where(BomLine.bom_id == bom.id)
            .options(selectinload(BomLine.substitutes))
            .order_by(BomLine.line_no)
        )).scalars().all())
        effective_lines = [ln for ln in all_lines if _covers(ln, date)]
        if effective_lines:
            return BomEffectiveResponse(
                id=bom.id,
                product_material_code=bom.product_material_code,
                bom_type=bom.bom_type,
                version=bom.version,
                factory_code=bom.factory_code,
                status=bom.status,
                yield_rate=bom.yield_rate,
                nc_source_pk=bom.nc_source_pk,
                lines=[BomLineResponse.model_validate(ln) for ln in effective_lines],
            )

    raise HTTPException(
        status_code=404,
        detail=f"No effective BOM found for product={product!r} date={date}",
    )


@router.post("/sync", response_model=BomSyncResponse)
async def trigger_bom_sync(
    db: AsyncSession = Depends(get_db),
    _: SyncDep = ...,
):
    return await sync_boms(db)
