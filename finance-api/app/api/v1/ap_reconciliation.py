"""AP reconciliation — our invoices against NC's books.

Read-only. Gated with the same `view_finance` read authorisation as the rest of
the finance reporting surface; nothing here writes, so there is no admin gate.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.nc_ap import NcApSyncRun
from app.services import ap_reconciliation as svc

router = APIRouter(prefix="/ap-recon", tags=["ap-recon"])

# Derived, never hand-listed: a category the summary can report but this set
# omits would be counted on screen and 422 on click, with the page showing
# "nothing in this category" and no sign that anything failed.
_CATEGORIES = set(svc.CATEGORIES)


def _filters(date_from: date | None, date_to: date | None,
             include_paid: bool) -> svc.ReconFilters:
    return svc.ReconFilters(date_from=date_from, date_to=date_to,
                            include_paid=include_paid)


async def _mirror_state(db: AsyncSession) -> dict:
    """When the mirror last moved, and whether it agreed with NC.

    Every answer on this page is only as good as the mirror behind it, so the
    freshness and the tie-out travel WITH the numbers rather than living on a
    separate admin screen nobody opens. A daily sync means a stale page is the
    normal case, not an edge case.
    """
    run = (await db.execute(
        select(NcApSyncRun).where(NcApSyncRun.status == "success")
        .order_by(NcApSyncRun.started_at.desc()).limit(1))).scalars().first()
    if run is None:
        return {"synced_at": None, "tie_out_ok": None, "stale": True}
    return {
        "synced_at": run.finished_at.isoformat() if run.finished_at else None,
        "tie_out_ok": run.tie_out_ok,
        "tie_out": run.tie_out,
        "stale": run.tie_out_ok is not True,
    }


@router.get("/summary")
async def summary(user: CurrentUser,
                  date_from: date | None = None,
                  date_to: date | None = None,
                  include_paid: bool = True,
                  db: AsyncSession = Depends(get_db)):
    data = await svc.summary(db, _filters(date_from, date_to, include_paid))
    data["mirror"] = await _mirror_state(db)
    return data


@router.get("/items")
async def items(user: CurrentUser,
                category: str = Query(...),
                date_from: date | None = None,
                date_to: date | None = None,
                include_paid: bool = True,
                limit: int = Query(200, ge=1, le=1000),
                offset: int = Query(0, ge=0),
                db: AsyncSession = Depends(get_db)):
    if category not in _CATEGORIES:
        raise HTTPException(status_code=422,
                            detail=f"unknown category; expected one of {sorted(_CATEGORIES)}")
    return await svc.items(db, _filters(date_from, date_to, include_paid),
                           category, limit=limit, offset=offset)


@router.get("/date-anomalies")
async def date_anomalies(user: CurrentUser,
                         date_from: date | None = None,
                         date_to: date | None = None,
                         include_paid: bool = True,
                         db: AsyncSession = Depends(get_db)):
    """Invoice dates that cannot be right — they feed the cash-flow buckets
    directly, so a day/month swap moves real money into the wrong month."""
    return await svc.date_anomalies(db, _filters(date_from, date_to, include_paid))
