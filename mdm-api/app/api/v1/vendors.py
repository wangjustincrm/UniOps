import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.crud import vendor as vendor_crud
from app.schemas.vendor import VendorListResponse, VendorResponse

router = APIRouter(prefix="/vendors", tags=["vendors"])


@router.get("", response_model=VendorListResponse)
async def list_vendors(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    search: str | None = Query(default=None),
    category: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    items, total = await vendor_crud.get_all(db, search=search, category=category, active_only=active_only, page=page, page_size=page_size)
    return VendorListResponse(items=items, total=total)


@router.get("/by-code/{code}", response_model=VendorResponse)
async def get_vendor_by_code(code: str, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    v = await vendor_crud.get_by_code(db, code)
    if not v:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return v


@router.get("/by-erp-id/{erp_id}", response_model=VendorResponse)
async def get_vendor_by_erp_id(erp_id: str, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    v = await vendor_crud.get_by_erp_id(db, erp_id)
    if not v:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return v


@router.get("/{vendor_id}", response_model=VendorResponse)
async def get_vendor(vendor_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    v = await vendor_crud.get_by_id(db, vendor_id)
    if not v:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return v
