"""AP cash flow — what is due, and when.

Read-only. Derived from the NC mirror on every request; there is no forecast
table to go stale. Sits under the same finance read surface as the rest of the
reporting pages.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.nc_ap import NcApSyncRun
from app.services import ap_cash_flow as svc

router = APIRouter(prefix="/ap-cash-flow", tags=["ap-cash-flow"])


@router.get("/summary")
async def summary(user: CurrentUser,
                  currency: str | None = Query(None),
                  db: AsyncSession = Depends(get_db)):
    data = await svc.summary(db, currency=currency)
    # The forecast is only as current as the mirror behind it, and the mirror
    # syncs on a schedule — so the freshness travels WITH the numbers rather
    # than living on an admin screen nobody opens.
    run = (await db.execute(
        select(NcApSyncRun).where(NcApSyncRun.status == "success")
        .order_by(NcApSyncRun.started_at.desc()).limit(1))).scalars().first()
    data["mirror"] = {
        "synced_at": run.finished_at.isoformat() if run and run.finished_at else None,
        "tie_out_ok": run.tie_out_ok if run else None,
    }
    return data


@router.get("/items")
async def items(user: CurrentUser,
                currency: str = Query(...),
                bucket: str | None = Query(None),
                limit: int = Query(500, ge=1, le=2000),
                offset: int = Query(0, ge=0),
                db: AsyncSession = Depends(get_db)):
    # Derived from the bucket ladder, never hand-listed: a bucket the summary
    # can report but this check rejects would be counted on screen and 422 on
    # click, with the table showing nothing and no sign anything failed.
    if bucket is not None and bucket not in svc.BUCKET_KEYS:
        raise HTTPException(status_code=422,
                            detail=f"bucket must be one of {sorted(svc.BUCKET_KEYS)}")
    return await svc.items(db, currency, bucket=bucket, limit=limit, offset=offset)


@router.get("/missing-terms")
async def missing_terms(user: CurrentUser,
                        currency: str | None = Query(None),
                        db: AsyncSession = Depends(get_db)):
    """The suppliers with no payment term on file — a work list, not a
    statistic. Each one is a vendor record or a term setting away from being
    forecastable, and together they hold 38% of the open balance."""
    return await svc.missing_terms(db, currency=currency)
