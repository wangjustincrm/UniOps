"""Plant area endpoints (mdm-api owns writes).

The tree has been read-only since it was seeded, because this service had no
endpoint for the table it owns. Reads are open to any authenticated caller —
every module that files anything against an area needs them — and writes are
behind `mdm.location.write`, which is held by HSE and system_admin.

There is no delete. An area is referenced by every record ever filed against
it and by the QR sticker on its door; it is retired instead.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.crud import location as crud
from app.db.base import get_db
from app.schemas.location import LocationCreate, LocationResponse, LocationUpdate

router = APIRouter(prefix="/locations", tags=["locations"])

WriteDep = Annotated[dict, Depends(require_permission("mdm.location.write"))]


@router.get("", response_model=list[LocationResponse])
async def list_locations(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    active_only: bool = Query(default=False),
):
    return await crud.get_all(db, active_only=active_only)


@router.post("", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
async def create_location(
    body: LocationCreate,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    if body.parent_id and not await crud.get(db, body.parent_id):
        raise HTTPException(status_code=404, detail="Parent area not found")
    if await crud.sibling_named(db, body.parent_id, body.name):
        raise HTTPException(
            status_code=409,
            detail=f"There is already an area called {body.name.strip()} here.",
        )
    return await crud.create(db, body.model_dump())


@router.patch("/{location_id}", response_model=LocationResponse)
async def update_location(
    location_id: uuid.UUID,
    body: LocationUpdate,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    node = await crud.get(db, location_id)
    if not node:
        raise HTTPException(status_code=404, detail="Area not found")
    if body.name and await crud.sibling_named(
            db, node.parent_id, body.name, excluding=node.id):
        raise HTTPException(
            status_code=409,
            detail=f"There is already an area called {body.name.strip()} here.",
        )
    return await crud.update(db, node, body.model_dump(exclude_unset=True))


@router.post("/{location_id}/retire", response_model=LocationResponse)
async def retire_location(
    location_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    node = await crud.get(db, location_id)
    if not node:
        raise HTTPException(status_code=404, detail="Area not found")
    return await crud.set_active(db, node, False)


@router.post("/{location_id}/restore", response_model=LocationResponse)
async def restore_location(
    location_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    node = await crud.get(db, location_id)
    if not node:
        raise HTTPException(status_code=404, detail="Area not found")
    return await crud.set_active(db, node, True)
