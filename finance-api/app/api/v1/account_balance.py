"""Account balance report API (科目余额表 能力①) — reads posted JV lines."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.crud import account_balance as crud
from app.db.base import get_db

router = APIRouter(prefix="/gl", tags=["account-balance"])


@router.get("/account-balance")
async def account_balance(_: CurrentUser, db: AsyncSession = Depends(get_db),
                          period: str = Query(...)):
    return await crud.account_balance(db, period)
