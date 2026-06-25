"""Custom form definition endpoints (CFM) — PRD-OA §12.

Registered BEFORE the expenses router in main.py so the literal
/expenses/custom-forms path is matched before /expenses/{claim_id} (UUID param).
"""
from fastapi import APIRouter, HTTPException, Query

from app.core.deps import CurrentUserDep, SessionDep
from app.crud import custom_form as cf_crud
from app.schemas.custom_form import (
    CustomFormCreate, CustomFormResponse, CustomFormUpdate,
)

router = APIRouter(prefix="/expenses/custom-forms", tags=["custom-forms"])

_ADMIN_ROLES = ("system_admin", "finance_manager")


@router.get("", response_model=list[CustomFormResponse])
async def list_custom_forms(
    db: SessionDep,
    _: CurrentUserDep,
    active_only: bool = Query(False),
):
    return await cf_crud.list_forms(db, active_only=active_only)


@router.post("", response_model=CustomFormResponse, status_code=201)
async def create_custom_form(body: CustomFormCreate, db: SessionDep, user: CurrentUserDep):
    if user.get("role") not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only System Admin or Finance Manager can manage custom forms")
    if await cf_crud.get_by_code(db, body.code):
        raise HTTPException(status_code=409, detail=f"Custom form code '{body.code}' already exists")
    return await cf_crud.create_form(db, body)


@router.patch("/{code}", response_model=CustomFormResponse)
async def update_custom_form(code: str, body: CustomFormUpdate, db: SessionDep, user: CurrentUserDep):
    if user.get("role") not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only System Admin or Finance Manager can manage custom forms")
    form = await cf_crud.get_by_code(db, code)
    if not form:
        raise HTTPException(status_code=404, detail=f"Custom form '{code}' not found")
    return await cf_crud.update_form(db, form, body)
