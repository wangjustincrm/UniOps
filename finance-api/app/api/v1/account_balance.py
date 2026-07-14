"""Account balance report API (科目余额表) — reads posted JV lines."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
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


@router.get("/account-balance/{account_code}/dims")
async def dims(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Checkable aux dimensions for an account (coa_aux_items ∩ registry)."""
    return await crud.list_dims(db, account_code)


@router.get("/account-balance/{account_code}/expand")
async def expand(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db),
                 period: str = Query(...),
                 dims: str = Query(default="cost_center")):
    """② dynamic expansion by a comma-separated dimension subset."""
    try:
        return await crud.expand_by_dims(db, account_code, period,
                                         [d.strip() for d in dims.split(",") if d.strip()])
    except crud.BadDims as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/account-balance/{account_code}/vouchers")
async def vouchers(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db),
                   period: str = Query(...),
                   dims_values: str | None = Query(default=None)):
    """③ drill-down; dims_values = 'dim:uuid,dim:none' combo filter."""
    parsed: dict = {}
    if dims_values:
        for pair in dims_values.split(","):
            if not pair.strip():
                continue
            if ":" not in pair:
                raise HTTPException(status_code=422, detail=f"bad dims_values pair: {pair!r}")
            d, v = pair.split(":", 1)
            try:
                parsed[d.strip()] = None if v.strip() == "none" else uuid.UUID(v.strip())
            except ValueError:
                raise HTTPException(status_code=422, detail=f"bad uuid in dims_values: {v!r}")
    try:
        return await crud.account_vouchers(db, account_code, period, dims_values=parsed)
    except crud.BadDims as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/budget-actual")
async def budget_actual(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        period: str = Query(...)):
    """④ Budget Actual: 4 expense accounts' actuals per cost center by category."""
    return await crud.budget_actual(db, period)
