"""NC65 accounts-payable sync — status + trigger (system_admin only for writes).

Mirrors api/v1/nc_sync.py (the voucher sync) endpoint for endpoint, with one
addition the payables domain needs: the status payload reports `tie_out` and
`tie_out_ok` separately from `status`. A run that finished but does not agree
with NC is NOT a green run — incremental sync cannot see rows deleted in NC, so
"the job succeeded" and "the mirror is complete" are different facts and the UI
has to be able to show the second one going red on its own.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.mirrors import CompanyConfig
from app.models.nc_ap import RUNNING, NcApSyncRun
from app.services import nc_ap_sync as svc
from app.tasks import nc_ap_sync_scheduler as sched

router = APIRouter(prefix="/nc-ap-sync", tags=["nc-ap-sync"])

# test seams — monkeypatched in tests to point at finance_test / a fake extract
_worker_dsn = svc._pg_dsn
_fetch = svc.fetch_from_nc


class SyncIn(BaseModel):
    mode: Literal["full", "incremental"]
    confirm: str | None = None


class IntervalIn(BaseModel):
    minutes: int


def _run_out(r: NcApSyncRun | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": str(r.id), "mode": r.mode, "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "watermark_from": r.watermark_from, "watermark_to": r.watermark_to,
        "bills_seen": r.bills_seen, "bills_upserted": r.bills_upserted,
        "lines_upserted": r.lines_upserted, "lines_deleted": r.lines_deleted,
        "tie_out_ok": r.tie_out_ok, "tie_out": r.tie_out,
        "error": r.error,
    }


@router.get("/status")
async def status(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    # sweep stale running rows so the UI never shows a permanently-stuck run
    await db.execute(text(
        "update nc_ap_sync_runs set status = 'failed', error = 'abandoned', "
        "finished_at = now(), updated_at = now() "
        "where status = 'running' and updated_at < :cutoff"),
        {"cutoff": datetime.now(timezone.utc) - svc.STALE_AFTER})
    await db.commit()
    current = (await db.execute(
        select(NcApSyncRun).where(NcApSyncRun.status == RUNNING)
        .order_by(NcApSyncRun.started_at.desc()).limit(1))).scalars().first()
    last = (await db.execute(
        select(NcApSyncRun).where(NcApSyncRun.status != RUNNING)
        .order_by(NcApSyncRun.started_at.desc()).limit(1))).scalars().first()
    interval = sched.resolve_interval_minutes((await db.execute(
        select(CompanyConfig.nc_ap_sync_interval_minutes).limit(1))
    ).scalar_one_or_none())
    # When the scheduler will next pick it up. Measured from the last run's
    # START, exactly like is_due() — a countdown computed from anything else
    # would disagree with the loop it claims to describe. Null when the
    # schedule is off, so the UI says "off" instead of drawing a countdown
    # that never arrives.
    anchor = current or last
    next_due_at = None
    if interval > 0 and anchor is not None and anchor.started_at is not None:
        started = anchor.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        next_due_at = (started + timedelta(minutes=interval)).isoformat()
    return {
        "can_sync": user.get("role") == "system_admin",
        "configured": svc.nc_configured(),
        "current_run": _run_out(current),
        "last_run": _run_out(last),
        "interval_minutes": interval,
        "next_due_at": next_due_at,
    }


@router.get("/runs")
async def runs(user: CurrentUser, limit: int = 20, db: AsyncSession = Depends(get_db)):
    """Run history — the audit trail for "when did this number last move"."""
    limit = max(1, min(limit, 100))
    rows = (await db.execute(
        select(NcApSyncRun).order_by(NcApSyncRun.started_at.desc()).limit(limit)
    )).scalars().all()
    return {"runs": [_run_out(r) for r in rows]}


@router.post("", status_code=202)
async def trigger(body: SyncIn, user: CurrentUser):
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
    if not svc.nc_configured():
        raise HTTPException(status_code=503, detail="NC connection is not configured")
    if body.mode == "full" and body.confirm != svc.FULL_CONFIRM:
        raise HTTPException(status_code=422,
                            detail=f'full reload requires confirm="{svc.FULL_CONFIRM}"')
    dsn = _worker_dsn()
    try:
        # gate + insert the running row synchronously (fast, DB-only)…
        run_id = svc.start_run(body.mode, uuid.UUID(user["sub"]),
                               fetch=_fetch, pg_dsn=dsn, confirm=body.confirm,
                               run_worker=False)
    except svc.SyncAlreadyRunning as e:
        raise HTTPException(status_code=409, detail=str(e))
    # …then do the actual NC read + load on a worker thread.
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, svc._run_worker, run_id, body.mode, _fetch, dsn)
    return {"run_id": str(run_id)}


@router.patch("/interval")
async def set_interval(body: IntervalIn, user: CurrentUser,
                       db: AsyncSession = Depends(get_db)):
    """How often the sync runs itself. 0 disables the schedule, leaving the
    button as the only trigger."""
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
    if isinstance(body.minutes, bool) or not 0 <= body.minutes <= sched.MAX_INTERVAL_MINUTES:
        raise HTTPException(
            status_code=422,
            detail=f"minutes must be between 0 and {sched.MAX_INTERVAL_MINUTES} "
                   f"(0 disables automatic sync)")
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalars().first()
    if cfg is None:
        raise HTTPException(status_code=404, detail="company config not found")
    cfg.nc_ap_sync_interval_minutes = body.minutes
    # finance-api's get_db() does not auto-commit — without this the change is
    # discarded.
    await db.commit()
    return {"interval_minutes": body.minutes}
