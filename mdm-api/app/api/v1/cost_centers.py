"""CostCenter endpoints (mdm-api owns writes).

Reads: any authenticated role. Writes: system_admin | finance_manager | ap_clerk.
Delete is blocked when the cost center is referenced by any PR (shared-DB count).
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.crud import cost_center as cc_crud
from app.crud import department as dept_crud
from app.db.base import get_db
from app.schemas.cost_center import CostCenterCreate, CostCenterResponse, CostCenterUpdate

router = APIRouter(prefix="/cost-centers", tags=["cost-centers"])

WriteDep = Annotated[dict, Depends(require_permission("mdm.finance.write"))]


@router.get("", response_model=list[CostCenterResponse])
async def list_cost_centers(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    department_id: uuid.UUID | None = Query(default=None),
    active_only: bool = Query(default=False),
):
    items = await cc_crud.get_all(db, department_id=department_id, active_only=active_only)
    return [CostCenterResponse.from_orm_with_dept(cc) for cc in items]


@router.post("", response_model=CostCenterResponse, status_code=status.HTTP_201_CREATED)
async def create_cost_center(
    body: CostCenterCreate,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    if await dept_crud.get_by_id(db, body.department_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    if await cc_crud.get_by_code(db, body.code):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Code already exists")
    cc = await cc_crud.create(db, body)
    return CostCenterResponse.from_orm_with_dept(cc)


@router.get("/by-code/{code}", response_model=CostCenterResponse)
async def get_cc_by_code(
    code: str,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    cc = await cc_crud.get_by_code(db, code)
    if not cc:
        raise HTTPException(status_code=404, detail="Cost center not found")
    return CostCenterResponse.from_orm_with_dept(cc)


@router.get("/{cc_id}", response_model=CostCenterResponse)
async def get_cost_center(
    cc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
):
    cc = await cc_crud.get_by_id(db, cc_id)
    if not cc:
        raise HTTPException(status_code=404, detail="Cost center not found")
    return CostCenterResponse.from_orm_with_dept(cc)


@router.patch("/{cc_id}", response_model=CostCenterResponse)
async def update_cost_center(
    cc_id: uuid.UUID,
    body: CostCenterUpdate,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    cc = await cc_crud.get_by_id(db, cc_id)
    if cc is None:
        raise HTTPException(status_code=404, detail="Cost center not found")
    if body.department_id and await dept_crud.get_by_id(db, body.department_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    cc = await cc_crud.update(db, cc, body)
    return CostCenterResponse.from_orm_with_dept(cc)


@router.delete("/{cc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cost_center(
    cc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: WriteDep = ...,
):
    cc = await cc_crud.get_by_id(db, cc_id)
    if cc is None:
        raise HTTPException(status_code=404, detail="Cost center not found")
    refs = await cc_crud.count_references(db, cc_id)
    if refs > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cost center is referenced by existing purchase requests or budget accounts and cannot be deleted. Deactivate it instead.",
        )
    await cc_crud.delete(db, cc)
