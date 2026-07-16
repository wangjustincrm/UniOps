"""Department endpoints (mdm-api owns writes).

Reads: any authenticated role. Writes: system_admin | finance_manager | ap_clerk.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.crud import department as dept_crud
from app.db.base import get_db
from app.schemas.department import (
    DepartmentCreate,
    DepartmentListResponse,
    DepartmentResponse,
    DepartmentUpdate,
)

router = APIRouter(prefix="/departments", tags=["departments"])

WriteDep = Annotated[dict, Depends(require_permission("mdm.finance.write"))]


@router.get("", response_model=DepartmentListResponse)
async def list_departments(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    active_only: bool = Query(default=False),
):
    items = await dept_crud.get_all(db, active_only=active_only)
    return DepartmentListResponse(items=items, total=len(items))


@router.post("", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
async def create_department(
    body: DepartmentCreate,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    if await dept_crud.get_by_code(db, body.code):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Code already exists")
    return await dept_crud.create(db, body)


@router.get("/by-code/{code}", response_model=DepartmentResponse)
async def get_department_by_code(
    code: str,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    d = await dept_crud.get_by_code(db, code)
    if not d:
        raise HTTPException(status_code=404, detail="Department not found")
    return d


@router.get("/{dept_id}", response_model=DepartmentResponse)
async def get_department(
    dept_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    d = await dept_crud.get_by_id(db, dept_id)
    if not d:
        raise HTTPException(status_code=404, detail="Department not found")
    return d


@router.patch("/{dept_id}", response_model=DepartmentResponse)
async def update_department(
    dept_id: uuid.UUID,
    body: DepartmentUpdate,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    dept = await dept_crud.get_by_id(db, dept_id)
    if dept is None:
        raise HTTPException(status_code=404, detail="Department not found")
    return await dept_crud.update(db, dept, body)


@router.delete("/{dept_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_department(
    dept_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    dept = await dept_crud.get_by_id(db, dept_id)
    if dept is None:
        raise HTTPException(status_code=404, detail="Department not found")
    refs = await dept_crud.count_references(db, dept_id)
    blocking = []
    if refs["cost_centers"] > 0:
        blocking.append(f"{refs['cost_centers']} cost center(s)")
    if refs["users"] > 0:
        blocking.append(f"{refs['users']} user(s)")
    if blocking:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Department has {' and '.join(blocking)} and cannot be deleted. Deactivate it instead.",
        )
    await dept_crud.delete(db, dept)
