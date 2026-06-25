"""Cost Center read-only endpoints.

Writes (POST/PATCH/DELETE) moved to mdm-api as of 2026-05-27 — see
`docs/superpowers/specs/2026-05-26-cost-center-dept-mdm-migration.md`. GETs
remain here so legacy EPMS callers (PR create/edit, Budget pages, Reports)
keep working during the transition; new callers should target
`mdm-api/mdm/v1/cost-centers`.
"""
import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import SessionDep
from app.crud import cost_center as cc_crud
from app.schemas.department import CostCenterResponse

router = APIRouter(prefix="/cost-centers", tags=["cost-centers"])


@router.get("", response_model=list[CostCenterResponse])
async def list_cost_centers(
    db: SessionDep,
    department_id: uuid.UUID | None = Query(default=None),
    active_only: bool = False,
):
    """List cost centers, optionally filtered by department."""
    return await cc_crud.get_all(db, department_id=department_id, active_only=active_only)


@router.get("/{cc_id}", response_model=CostCenterResponse)
async def get_cost_center(cc_id: uuid.UUID, db: SessionDep):
    cc = await cc_crud.get_by_id(db, cc_id)
    if cc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cost center not found")
    return cc
