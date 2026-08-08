"""UOM conversion factors, mirrored from NC ERP unitTranf (MRP phase0 task 3).

Reads: any authenticated role (matches materials.py/uom.py). Writes (sync):
gated with require_permission("data_maintenance"), same pattern as
materials.py's POST /materials/sync.
"""
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.uom_conversion import UomConversion
from app.services.erp_sync import sync_uom_conversions

router = APIRouter(prefix="/uom-conversions", tags=["uom-conversions"])

SyncDep = Annotated[dict, Depends(require_permission("data_maintenance"))]


class UomConversionResponse(BaseModel):
    id: uuid.UUID
    from_uom: str
    to_uom: str
    rate: Decimal

    model_config = {"from_attributes": True}


class UomConversionListResponse(BaseModel):
    items: list[UomConversionResponse]
    total: int


class UomConversionSyncResponse(BaseModel):
    kind: str
    mode: str
    total: int
    inserted: int
    updated: int
    last_ts: str
    status: str
    message: str


@router.get("", response_model=UomConversionListResponse)
async def list_uom_conversions(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    items = list((await db.execute(
        select(UomConversion).order_by(UomConversion.from_uom, UomConversion.to_uom)
    )).scalars().all())
    return UomConversionListResponse(items=items, total=len(items))


@router.post("/sync", response_model=UomConversionSyncResponse)
async def trigger_uom_conversion_sync(
    db: AsyncSession = Depends(get_db),
    _: SyncDep = ...,
):
    return await sync_uom_conversions(db)
