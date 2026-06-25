import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.crud import part as part_crud
from app.schemas.part import PartListResponse, PartResponse

router = APIRouter(prefix="/parts", tags=["parts"])


@router.get("", response_model=PartListResponse)
async def list_parts(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    search: str | None = Query(default=None),
    category: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    items, total = await part_crud.get_all(db, search=search, category=category, active_only=active_only, page=page, page_size=page_size)
    return PartListResponse(items=items, total=total)


@router.get("/by-code/{code}", response_model=PartResponse)
async def get_part_by_code(code: str, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    p = await part_crud.get_by_code(db, code)
    if not p:
        raise HTTPException(status_code=404, detail="Part not found")
    return p


@router.get("/{part_id}", response_model=PartResponse)
async def get_part(part_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    p = await part_crud.get_by_id(db, part_id)
    if not p:
        raise HTTPException(status_code=404, detail="Part not found")
    return p
