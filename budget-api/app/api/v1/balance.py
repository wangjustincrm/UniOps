"""Balance query — used by epms-api for PR over-budget check."""
import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.crud import balance as balance_crud
from app.crud import catalog as catalog_crud
from app.schemas.balance import BalanceResponse

router = APIRouter(tags=["balance"])


@router.get("/balance", response_model=BalanceResponse)
async def get_balance(
    db: SessionDep, user: CurrentUserPayload, token: BearerToken,  # noqa: ARG001
    cost_center_id: uuid.UUID = Query(...),
    fiscal_year: int = Query(..., ge=2020, le=2100),
    account_id: uuid.UUID | None = Query(default=None),
    account_code: str | None = Query(default=None),
):
    if account_id is None and account_code is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Either account_id or account_code is required",
        )
    if account_id is None:
        acct = await catalog_crud.get_account_by_code(db, account_code)
        if acct is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Account code '{account_code}' not found")
        account_id = acct.id

    try:
        return await balance_crud.get_balance(
            db, cost_center_id, account_id, fiscal_year, bearer_token=token,
        )
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
