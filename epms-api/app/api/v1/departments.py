"""Department read-only endpoints.

Writes (POST/PATCH/DELETE) moved to mdm-api as of 2026-05-27 — see
`docs/superpowers/specs/2026-05-26-cost-center-dept-mdm-migration.md`. GETs
remain here so legacy EPMS callers (PR create page, user forms) keep working
during the transition; new callers should target `mdm-api/mdm/v1/departments`.
"""
import uuid

from fastapi import APIRouter, HTTPException, status

from app.core.deps import SessionDep
from app.crud import department as dept_crud
from app.schemas.department import DepartmentListResponse, DepartmentResponse

router = APIRouter(prefix="/departments", tags=["departments"])


@router.get("", response_model=DepartmentListResponse)
async def list_departments(db: SessionDep, active_only: bool = False):
    """List all departments. Any authenticated call (no extra role needed)."""
    items = await dept_crud.get_all(db, active_only=active_only)
    return DepartmentListResponse(items=items, total=len(items))


@router.get("/{dept_id}", response_model=DepartmentResponse)
async def get_department(dept_id: uuid.UUID, db: SessionDep):
    dept = await dept_crud.get_by_id(db, dept_id)
    if dept is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    return dept
