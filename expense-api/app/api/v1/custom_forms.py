"""Custom form definition endpoints (CFM) — PRD-OA §12.

Registered BEFORE the expenses router in main.py so the literal
/expenses/custom-forms path is matched before /expenses/{claim_id} (UUID param).
"""
import uuid

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
    user: CurrentUserDep,
    active_only: bool = Query(False),
):
    if user.get("role") not in _ADMIN_ROLES:
        active_only = True
    from app.api.v1.expenses import _action_key, _get_workflow_defs

    wf = await _get_workflow_defs(db)
    out = []
    for form in await cf_crud.list_forms(db, active_only=active_only):
        r = CustomFormResponse.model_validate(form)
        key = _action_key(f"CFM_{form.code}")
        r.effective_workflow_key = key if wf.get(key) else "cfm"
        out.append(r)
    return out


@router.get("/for-claim/{claim_id}", response_model=CustomFormResponse)
async def custom_form_for_claim(claim_id: uuid.UUID, db: SessionDep, user: CurrentUserDep):
    """The form a CFM claim was raised on, active or not — for its edit page.

    The list above shows employees active forms only (OA.md §6.1: inactive
    forms are hidden but kept). A draft or returned claim raised before its
    form was deactivated still has to be editable (Q-05), so whoever may edit
    that claim gets that one form's definition; nobody can list inactive
    forms through it.
    """
    from app.api.v1.expenses import can_edit_claim
    from app.crud import expense as expense_crud

    claim = await expense_crud.get_by_id(db, claim_id)
    if not claim or not claim.claim_type.upper().startswith("CFM_"):
        raise HTTPException(status_code=404, detail="Custom form claim not found")
    if not can_edit_claim(claim, uuid.UUID(user["sub"]), user.get("role", "")):
        raise HTTPException(status_code=403, detail="Cannot edit another employee's claim")
    form = await cf_crud.get_by_code(db, claim.claim_type[len("CFM_"):])
    if not form:
        raise HTTPException(status_code=404, detail="Custom form not found")
    return form


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
