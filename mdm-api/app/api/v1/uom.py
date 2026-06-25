"""Unit of Measure endpoints (mdm-api owns writes).

Reads: any authenticated role. Writes: system_admin | finance_manager | ap_clerk.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_roles
from app.crud import uom as uom_crud
from app.db.base import get_db
from app.schemas.uom import UomCreate, UomListResponse, UomResponse, UomUpdate

router = APIRouter(prefix="/uom", tags=["uom"])

WriteDep = Annotated[dict, Depends(require_roles("system_admin", "finance_manager", "ap_clerk"))]


@router.get("", response_model=UomListResponse)
async def list_uoms(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    active_only: bool = Query(default=False),
):
    items = await uom_crud.get_all(db, active_only=active_only)
    return UomListResponse(items=items, total=len(items))


@router.post("", response_model=UomResponse, status_code=status.HTTP_201_CREATED)
async def create_uom(
    body: UomCreate,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    if await uom_crud.get_by_code(db, body.code):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Code already exists")
    return await uom_crud.create(db, body)


@router.get("/by-code/{code}", response_model=UomResponse)
async def get_uom_by_code(
    code: str,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    u = await uom_crud.get_by_code(db, code)
    if not u:
        raise HTTPException(status_code=404, detail="Unit of measure not found")
    return u


@router.get("/{uom_id}", response_model=UomResponse)
async def get_uom(
    uom_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    u = await uom_crud.get_by_id(db, uom_id)
    if not u:
        raise HTTPException(status_code=404, detail="Unit of measure not found")
    return u


@router.patch("/{uom_id}", response_model=UomResponse)
async def update_uom(
    uom_id: uuid.UUID,
    body: UomUpdate,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    u = await uom_crud.get_by_id(db, uom_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Unit of measure not found")
    return await uom_crud.update(db, u, body)


@router.delete("/{uom_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_uom(
    uom_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    u = await uom_crud.get_by_id(db, uom_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Unit of measure not found")
    refs = await uom_crud.count_references(db, u.code)
    if refs["parts"] > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Unit is used by {refs['parts']} part(s) and cannot be deleted. Deactivate it instead.",
        )
    await uom_crud.delete(db, u)
