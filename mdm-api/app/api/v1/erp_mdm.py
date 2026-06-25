"""ERP MDM endpoints: trigger sync, browse mirrors, by-code lookups."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.crud import erp as erp_crud
from app.models.erp_sync_state import ErpSyncState
from app.schemas.erp import (
    ErpMaterialListResponse, ErpMaterialResponse,
    ErpSupplierListResponse, ErpSupplierResponse,
    ErpPersonListResponse, ErpPersonResponse,
    ErpSyncStateResponse, ErpSyncResultResponse,
)
from app.services.erp_client import ErpError
from app.services.erp_sync import sync_kind

router = APIRouter(prefix="/erp", tags=["erp-mdm"])


def _require_admin(user: dict) -> None:
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin required")


def _require_admin_or(user: dict, *roles: str) -> None:
    role = user.get("role")
    if role != "system_admin" and role not in roles:
        raise HTTPException(status_code=403, detail=f"requires one of: system_admin, {','.join(roles)}")


@router.post("/sync/{kind}", response_model=ErpSyncResultResponse)
async def trigger_sync(
    kind: str,
    full: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = None,
):
    _require_admin(user)
    if kind not in ("material", "supplier", "person"):
        raise HTTPException(status_code=400, detail="kind must be material|supplier|person")
    try:
        result = await sync_kind(db, kind, full=full)
    except ErpError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result


@router.get("/sync/status")
async def sync_status(db: AsyncSession = Depends(get_db), user: CurrentUser = None):
    _require_admin(user)
    rows = (await db.execute(select(ErpSyncState))).scalars().all()
    out: dict[str, dict | None] = {"material": None, "supplier": None, "person": None}
    for r in rows:
        out[r.kind] = ErpSyncStateResponse.model_validate(r).model_dump()
    return out


@router.get("/materials", response_model=ErpMaterialListResponse)
async def list_materials(
    search: str | None = Query(default=None),
    part_status: str | None = Query(default=None),
    item_mes_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = None,
):
    items, total = await erp_crud.list_materials(
        db, search=search, part_status=part_status, item_mes_type=item_mes_type,
        page=page, page_size=page_size,
    )
    return {"items": items, "total": total}


@router.get("/materials/{code}", response_model=ErpMaterialResponse)
async def get_material(code: str, db: AsyncSession = Depends(get_db), user: CurrentUser = None):
    m = await erp_crud.get_material_by_code(db, code)
    if not m:
        raise HTTPException(status_code=404, detail="material not found")
    return m


@router.get("/suppliers", response_model=ErpSupplierListResponse)
async def list_suppliers(
    search: str | None = Query(default=None),
    exclude_codes: str | None = Query(default=None, description="comma-separated codes to exclude"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = None,
):
    _require_admin_or(user, "vendor_manager")
    excludes = [c.strip() for c in exclude_codes.split(",")] if exclude_codes else None
    items, total = await erp_crud.list_suppliers(
        db, search=search, exclude_codes=excludes, page=page, page_size=page_size,
    )
    return {"items": items, "total": total}


@router.get("/suppliers/{code}", response_model=ErpSupplierResponse)
async def get_supplier(code: str, db: AsyncSession = Depends(get_db), user: CurrentUser = None):
    _require_admin_or(user, "vendor_manager")
    s = await erp_crud.get_supplier_by_code(db, code)
    if not s:
        raise HTTPException(status_code=404, detail="supplier not found")
    return s


@router.get("/persons", response_model=ErpPersonListResponse)
async def list_persons(
    search: str | None = Query(default=None),
    exclude_codes: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = None,
):
    _require_admin(user)
    excludes = [c.strip() for c in exclude_codes.split(",")] if exclude_codes else None
    items, total = await erp_crud.list_persons(
        db, search=search, exclude_codes=excludes, page=page, page_size=page_size,
    )
    return {"items": items, "total": total}


@router.get("/persons/{code}", response_model=ErpPersonResponse)
async def get_person(code: str, db: AsyncSession = Depends(get_db), user: CurrentUser = None):
    _require_admin(user)
    p = await erp_crud.get_person_by_code(db, code)
    if not p:
        raise HTTPException(status_code=404, detail="person not found")
    return p
