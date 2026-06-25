"""Expense policy configuration endpoint."""
from fastapi import APIRouter, HTTPException

from app.core.deps import CurrentUserDep, SessionDep
from app.crud import policy as policy_crud
from app.schemas.policy import ExpensePolicyResponse, ExpensePolicyUpdate

router = APIRouter(prefix="/policy", tags=["policy"])


@router.get("", response_model=ExpensePolicyResponse)
async def get_policy(db: SessionDep, _: CurrentUserDep):
    return ExpensePolicyResponse.model_validate(await policy_crud.get_policy(db))


@router.patch("", response_model=ExpensePolicyResponse)
async def update_policy(body: ExpensePolicyUpdate, db: SessionDep, user: CurrentUserDep):
    if user.get("role") not in ("finance_manager", "system_admin"):
        raise HTTPException(status_code=403, detail="Only Finance Manager or System Admin can update policy")
    policy = await policy_crud.update_policy(db, body)
    return ExpensePolicyResponse.model_validate(policy)
