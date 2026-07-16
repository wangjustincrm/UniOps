"""Fiscal period management — FIN-GL-002 soft/hard close (Phase 0-B1.7).

Close/reopen requires the finance.period.close permission (Access Control
matrix — default granted to finance_manager/system_admin). Hard-closed
periods cannot be reopened from the API (Controller decision per PRD; manual
DB action with audit trail if ever needed).
"""
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.fiscal_period import HARD_CLOSED, OPEN, SOFT_CLOSED, FiscalPeriod

router = APIRouter(prefix="/periods", tags=["fiscal-periods"])

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class PeriodOut(BaseModel):
    period: str
    status: str
    closed_at: datetime | None
    closed_by: uuid.UUID | None

    model_config = {"from_attributes": True}


class CloseRequest(BaseModel):
    hard: bool = False


def _validate_period(period: str) -> None:
    if not _PERIOD_RE.match(period):
        raise HTTPException(status_code=422, detail="Period must be 'YYYY-MM'")


@router.get("", response_model=list[PeriodOut])
async def list_periods(
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(FiscalPeriod).order_by(FiscalPeriod.period.desc()).limit(36)
    )).scalars().all()
    return rows


@router.post("/{period}/close", response_model=PeriodOut)
async def close_period(
    period: str,
    body: CloseRequest,
    user: dict = Depends(require_permission("finance.period.close")),
    db: AsyncSession = Depends(get_db),
):
    _validate_period(period)
    row = (await db.execute(
        select(FiscalPeriod).where(FiscalPeriod.period == period)
    )).scalar_one_or_none()
    target = HARD_CLOSED if body.hard else SOFT_CLOSED
    if row is None:
        row = FiscalPeriod(period=period)
        db.add(row)
    if row.status == HARD_CLOSED:
        raise HTTPException(status_code=409, detail=f"Period {period} is already hard-closed")
    row.status = target
    row.closed_at = datetime.now(timezone.utc)
    row.closed_by = uuid.UUID(user["sub"])
    await db.flush()
    await db.commit()
    return row


@router.post("/{period}/reopen", response_model=PeriodOut)
async def reopen_period(
    period: str,
    user: dict = Depends(require_permission("finance.period.close")),
    db: AsyncSession = Depends(get_db),
):
    _validate_period(period)
    row = (await db.execute(
        select(FiscalPeriod).where(FiscalPeriod.period == period)
    )).scalar_one_or_none()
    if row is None or row.status == OPEN:
        raise HTTPException(status_code=409, detail=f"Period {period} is not closed")
    if row.status == HARD_CLOSED:
        raise HTTPException(status_code=409, detail="Hard-closed periods cannot be reopened via API")
    row.status = OPEN
    row.closed_at = None
    row.closed_by = None
    await db.flush()
    await db.commit()
    return row
