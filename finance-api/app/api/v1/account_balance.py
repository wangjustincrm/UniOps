"""Account balance report API (科目余额表) — reads posted JV lines."""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.crud import account_balance as crud
from app.db.base import get_db

router = APIRouter(prefix="/gl", tags=["account-balance"])


@router.get("/account-balance")
async def account_balance(_: CurrentUser, db: AsyncSession = Depends(get_db),
                          period: str = Query(...)):
    """① 科目余额表: opening/period/closing per account (posted JV, local CAD)."""
    return await crud.account_balance(db, period)


@router.get("/account-balance/{account_code}/expand")
async def expand(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db),
                 period: str = Query(...)):
    """② 辅助核算展开 (by cost center) for one account."""
    return await crud.expand_by_cost_center(db, account_code, period)


@router.get("/account-balance/{account_code}/vouchers")
async def vouchers(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db),
                   period: str = Query(...),
                   cost_center_id: uuid.UUID | None = Query(default=None)):
    """③ 凭证钻取: posted JV lines composing an account (+optional cost center)."""
    return await crud.account_vouchers(db, account_code, period, cost_center_id)


@router.get("/budget-actual")
async def budget_actual(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        period: str = Query(...)):
    """④ Budget Actual: 4 expense accounts' actuals per cost center by category."""
    return await crud.budget_actual(db, period)
