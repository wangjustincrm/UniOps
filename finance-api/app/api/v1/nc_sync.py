"""NC65 voucher sync — status + trigger (system_admin only for writes)."""
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
from app.models.nc_sync import RUNNING, NcSyncRun
from app.services import nc_sync as svc
from app.tasks import nc_sync_scheduler as sched

router = APIRouter(prefix="/nc-sync", tags=["nc-sync"])

# test seams — monkeypatched in tests to point at finance_test / a fake extract
_worker_dsn = svc._pg_dsn
_fetch = svc.fetch_from_nc


class SyncIn(BaseModel):
    mode: Literal["full", "incremental"]
    confirm: str | None = None


def _run_out(r: NcSyncRun | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": str(r.id), "mode": r.mode, "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "watermark_from": r.watermark_from, "watermark_to": r.watermark_to,
        "vouchers_deleted": r.vouchers_deleted, "vouchers_inserted": r.vouchers_inserted,
        "lines_inserted": r.lines_inserted, "dims_inserted": r.dims_inserted,
        "unmapped_cc_count": r.unmapped_cc_count, "error": r.error,
    }


@router.get("/status")
async def status(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    # sweep stale running rows so the UI never shows a permanently-stuck run
    await db.execute(text(
        "update nc_sync_runs set status = 'failed', error = 'abandoned', "
        "finished_at = now(), updated_at = now() "
        "where status = 'running' and updated_at < :cutoff"),
        {"cutoff": datetime.now(timezone.utc) - svc.STALE_AFTER})
    await db.commit()
    current = (await db.execute(
        select(NcSyncRun).where(NcSyncRun.status == RUNNING)
        .order_by(NcSyncRun.started_at.desc()).limit(1))).scalars().first()
    last = (await db.execute(
        select(NcSyncRun).where(NcSyncRun.status != RUNNING)
        .order_by(NcSyncRun.started_at.desc()).limit(1))).scalars().first()
    interval = sched.resolve_interval_minutes((await db.execute(
        select(CompanyConfig.nc_jv_sync_interval_minutes).limit(1))
    ).scalar_one_or_none())
    # 调度器下一次会捡起它的时刻。和 is_due() 一样从上次 run 的 START 量 ——
    # 用别的东西算出来的倒计时会和它所描述的那个循环对不上。
    # 排期关闭时为 null,好让 UI 说"off",而不是画一个永远走不到的倒计时。
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
                               fetch=_fetch, pg_dsn=dsn, run_worker=False)
    except svc.SyncAlreadyRunning as e:
        raise HTTPException(status_code=409, detail=str(e))
    # …then do the actual NC read + load on a worker thread.
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, svc._run_worker, run_id, body.mode, _fetch, dsn)
    return {"run_id": str(run_id)}


class IntervalIn(BaseModel):
    minutes: int


@router.patch("/interval")
async def set_interval(body: IntervalIn, user: CurrentUser,
                       db: AsyncSession = Depends(get_db)):
    """同步多久自己跑一次。0 关闭排期,只剩按钮这一个触发方式。"""
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
    cfg.nc_jv_sync_interval_minutes = body.minutes
    # finance-api 的 get_db() 不自动 commit —— 不显式提交这里的改动会被丢弃。
    await db.commit()
    return {"interval_minutes": body.minutes}
