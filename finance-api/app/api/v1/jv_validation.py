"""JV dimension validation API — which posted lines cannot reach a budget cell.

Read-only; same auth tier as the rest of the GL reports (CurrentUser). No cost
center scoping here on purpose: the whole point of the page is the lines that
belong to NO cost center, and a department-scoped view of those is empty by
construction. Finance owns the fix (the NC entry, the mapping table, the budget
account catalog), so finance sees all of it.
"""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.services import jv_validation as svc

router = APIRouter(prefix="/gl/jv-validation", tags=["jv-validation"])

_PERIOD_HELP = "fiscal period 'YYYY-MM'"


def _check_period(value: str, name: str) -> str:
    if len(value) != 7 or value[4] != "-" or not value[:4].isdigit() \
            or not value[5:].isdigit() or not 1 <= int(value[5:]) <= 12:
        raise HTTPException(status_code=422, detail=f"{name} must be {_PERIOD_HELP}")
    return value


@router.get("")
async def validation_summary(_: CurrentUser, db: AsyncSession = Depends(get_db),
                             period_from: str = Query(...),
                             period_to: str = Query(...)):
    """Per-rule counts and amounts for a period range."""
    period_from = _check_period(period_from, "period_from")
    period_to = _check_period(period_to, "period_to")
    if period_from > period_to:
        raise HTTPException(status_code=422, detail="period_from is after period_to")
    return {"period_from": period_from, "period_to": period_to,
            "rules": await svc.summary(db, period_from, period_to)}


@router.get("/lines")
async def validation_lines(_: CurrentUser, db: AsyncSession = Depends(get_db),
                           period_from: str = Query(...),
                           period_to: str = Query(...),
                           rule: str | None = Query(default=None),
                           limit: int = Query(default=200, ge=1, le=2000),
                           offset: int = Query(default=0, ge=0)):
    """One page of flagged lines. A line appears once per rule it breaks."""
    period_from = _check_period(period_from, "period_from")
    period_to = _check_period(period_to, "period_to")
    if period_from > period_to:
        raise HTTPException(status_code=422, detail="period_from is after period_to")
    if rule is not None and rule not in svc.RULES:
        raise HTTPException(status_code=422, detail=f"unknown rule {rule!r}")
    return await svc.rows(db, period_from, period_to, rule=rule,
                          limit=limit, offset=offset)
