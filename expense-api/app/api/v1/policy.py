"""Expense policy configuration endpoint."""
from fastapi import APIRouter, HTTPException

from app.core.deps import CurrentUserDep, SessionDep
from app.crud import policy as policy_crud
from app.schemas.policy import ExpensePolicyResponse, ExpensePolicyUpdate

router = APIRouter(prefix="/policy", tags=["policy"])


@router.get("", response_model=ExpensePolicyResponse)
async def get_policy(db: SessionDep, _: CurrentUserDep):
    return ExpensePolicyResponse.model_validate(await policy_crud.get_policy(db))


_CAN_EDIT_POLICY = {"finance_manager", "system_admin"}


@router.patch("", response_model=ExpensePolicyResponse)
async def update_policy(body: ExpensePolicyUpdate, db: SessionDep, user: CurrentUserDep):
    """Mileage rate and meal per-diems — a finance rule, so Finance Manager
    edits it as well as system_admin.

    Checked against the role UNION, not the JWT's primary role alone: in this
    deployment finance_manager is frequently an ADDITIONAL role, and the
    primary-role-only test refused the very people whose job this is. Same
    union every other gate in the service uses.
    """
    import uuid as _uuid
    from app.api.v1.expenses import _user_role_codes

    codes = await _user_role_codes(db, _uuid.UUID(user["sub"]), user.get("role", ""))
    if not (codes & _CAN_EDIT_POLICY):
        raise HTTPException(status_code=403, detail="Only Finance Manager or System Admin can update policy")
    policy = await policy_crud.update_policy(db, body)
    return ExpensePolicyResponse.model_validate(policy)
