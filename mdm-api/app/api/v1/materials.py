"""Materials master table endpoints.

Reads: any authenticated role (matches parts.py). Writes: parts.py has no
write endpoint to mirror a gate from, so POST /materials/sync is gated with
require_permission("data_maintenance"), same authz.bind() pattern used by
uom.py/tax.py/departments.py/cost_centers.py in this service.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.material import Material
from app.services.material_sync import sync_materials

router = APIRouter(prefix="/materials", tags=["materials"])

SyncDep = Annotated[dict, Depends(require_permission("data_maintenance"))]


class MaterialResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str | None
    spec: str | None
    item_type: str | None
    erp_item_type: str | None
    base_uom: str | None
    shelf_life_months: int | None
    procurement_type: str
    product_family: str | None
    factory_code: str | None
    erp_id: str | None
    # ERP/NC material classification (物料基本分类). '0101' = Raw Milk, which
    # MRP excludes from every stock and on-order figure. Exposed because
    # classifying by code prefix is wrong here: CR0059 "Pasteurized Milk" is
    # 0101 while carrying an ordinary raw-material prefix.
    erp_class_code: str | None
    erp_class_name: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class MaterialListResponse(BaseModel):
    items: list[MaterialResponse]
    total: int
    page: int
    page_size: int


class MaterialSyncResponse(BaseModel):
    created: int
    updated: int
    skipped: int


@router.get("", response_model=MaterialListResponse)
async def list_materials(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    q: str | None = Query(default=None),
    item_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    query = select(Material)
    if q:
        term = f"%{q}%"
        query = query.where(or_(Material.code.ilike(term), Material.name.ilike(term)))
    if item_type:
        query = query.where(Material.item_type == item_type)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = list((await db.execute(
        query.order_by(Material.code).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return MaterialListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/sync", response_model=MaterialSyncResponse)
async def trigger_material_sync(
    db: AsyncSession = Depends(get_db),
    _: SyncDep = ...,
):
    return await sync_materials(db)
