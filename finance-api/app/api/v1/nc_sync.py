"""NC65 voucher sync — status + trigger (system_admin only for writes)."""
import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.nc_sync import RUNNING, NcSyncRun
from app.services import nc_sync as svc

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
    current = (await db.execute(
        select(NcSyncRun).where(NcSyncRun.status == RUNNING)
        .order_by(NcSyncRun.started_at.desc()).limit(1))).scalars().first()
    last = (await db.execute(
        select(NcSyncRun).where(NcSyncRun.status != RUNNING)
        .order_by(NcSyncRun.started_at.desc()).limit(1))).scalars().first()
    return {
        "can_sync": user.get("role") == "system_admin",
        "configured": svc.nc_configured(),
        "current_run": _run_out(current),
        "last_run": _run_out(last),
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
