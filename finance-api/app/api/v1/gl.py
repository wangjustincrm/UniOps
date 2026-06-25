"""General Ledger API (Phase f) — trial balance, account ledger, journal,
financial statements, opening balances, year-end close."""
import re
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.coa import _require_manage
from app.core.deps import CurrentUser
from app.crud import gl as gl_crud
from app.db.base import get_db

router = APIRouter(prefix="/gl", tags=["general-ledger"])

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _period(period: str) -> str:
    if not _PERIOD_RE.match(period):
        raise HTTPException(status_code=422, detail="period must be 'YYYY-MM'")
    return period


# ── reporting ──────────────────────────────────────────────────────────────────

@router.get("/trial-balance")
async def trial_balance(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        period: str = Query(...)):
    return await gl_crud.trial_balance(db, _period(period))


@router.get("/account/{code}")
async def account_ledger(code: str, _: CurrentUser, db: AsyncSession = Depends(get_db),
                         period: str = Query(...)):
    return await gl_crud.account_ledger(db, code, _period(period))


@router.get("/journal")
async def journal(_: CurrentUser, db: AsyncSession = Depends(get_db),
                  period: str = Query(...), limit: int = Query(default=200, le=1000)):
    return await gl_crud.journal(db, _period(period), limit=limit)


@router.get("/income-statement")
async def income_statement(_: CurrentUser, db: AsyncSession = Depends(get_db),
                           period: str = Query(...), ytd: bool = Query(default=True)):
    return await gl_crud.income_statement(db, _period(period), ytd=ytd)


@router.get("/balance-sheet")
async def balance_sheet(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        period: str = Query(...)):
    return await gl_crud.balance_sheet(db, _period(period))


# ── opening balances + year-end close ───────────────────────────────────────────

class OpeningLine(BaseModel):
    account_code: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")


class OpeningRequest(BaseModel):
    as_of: date
    lines: list[OpeningLine]


@router.post("/opening-balance")
async def post_opening_balance(body: OpeningRequest, user: CurrentUser,
                               db: AsyncSession = Depends(get_db)):
    """Post a migrated (NC65) trial balance as one balanced opening journal."""
    await _require_manage(db, user)
    try:
        result = await gl_crud.post_opening_balance(
            db, as_of=body.as_of, lines=[l.model_dump() for l in body.lines])
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


class CloseYearRequest(BaseModel):
    fiscal_year: int
    retained_earnings_code: str = gl_crud.RETAINED_EARNINGS


@router.post("/close-year")
async def close_year(body: CloseYearRequest, user: CurrentUser,
                     db: AsyncSession = Depends(get_db)):
    """Sweep revenue/expense into Retained Earnings, zeroing P&L for the new year."""
    await _require_manage(db, user)
    try:
        result = await gl_crud.close_year(
            db, fiscal_year=body.fiscal_year, retained_earnings_code=body.retained_earnings_code)
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
