"""NC purchase (order + arrival) sync — status + trigger (system_admin only for
writes). Mirrors finance-api/app/api/v1/nc_sync.py, adapted to this repo's
CurrentUserPayload / SessionDep dependency names and the nc_purchase_sync_runs
registry (app.models.nc_purchase_sync.NcPurchaseSyncRun).

Cutover has exactly ONE source: app.services.nc_purchase_sync.service._cutover()
(settings.nc_purchase_cutover, falling back to that module's wide-open default).
_run_worker already threads it into `fetch(cutover, watermark)` — this router
just passes reader.fetch_nc straight through and echoes the effective cutover.
"""
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text

from app.core.deps import CurrentUserPayload, SessionDep, require_roles
from app.models.nc_purchase_sync import RUNNING, NcPurchaseSyncRun
from app.services.nc_purchase_sync import reader
from app.services.nc_purchase_sync import service as svc

router = APIRouter(prefix="/admin/nc-purchase-sync", tags=["nc-purchase-sync"])

# Reuse the existing system_admin dependency (same pattern as projects.py /
# users.py) — applies to the POST trigger only. GET /status stays open to any
# authenticated user; it computes can_sync from the caller's own role.
AdminDep = Annotated[dict, Depends(require_roles("system_admin"))]


class SyncIn(BaseModel):
    mode: Literal["full", "incremental"]
    confirm: str | None = None


def _run_out(r: NcPurchaseSyncRun | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": str(r.id), "mode": r.mode, "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "watermark_from": r.watermark_from, "watermark_to": r.watermark_to,
        "pos_upserted": r.pos_upserted, "po_lines_upserted": r.po_lines_upserted,
        "grs_upserted": r.grs_upserted, "gr_lines_upserted": r.gr_lines_upserted,
        "skipped_no_vendor": r.skipped_no_vendor, "skipped_consumed": r.skipped_consumed,
        "error": r.error,
    }


@router.get("/status")
async def status(user: CurrentUserPayload, db: SessionDep):
    # sweep stale running rows so the UI never shows a permanently-stuck run
    await db.execute(text(
        "update nc_purchase_sync_runs set status = 'failed', error = 'abandoned', "
        "finished_at = now(), updated_at = now() "
        "where status = 'running' and updated_at < :cutoff"),
        {"cutoff": datetime.now(timezone.utc) - svc.STALE_AFTER})
    await db.commit()
    current = (await db.execute(
        select(NcPurchaseSyncRun).where(NcPurchaseSyncRun.status == RUNNING)
        .order_by(NcPurchaseSyncRun.started_at.desc()).limit(1))).scalars().first()
    last = (await db.execute(
        select(NcPurchaseSyncRun).where(NcPurchaseSyncRun.status != RUNNING)
        .order_by(NcPurchaseSyncRun.started_at.desc()).limit(1))).scalars().first()
    return {
        "configured": svc.nc_configured(),
        "can_sync": user.get("role") == "system_admin",
        "cutover": svc._cutover(),
        "current_run": _run_out(current),
        "last_run": _run_out(last),
    }


@router.post("", status_code=202)
async def trigger(body: SyncIn, user: AdminDep):
    if not svc.nc_configured():
        raise HTTPException(status_code=503, detail="NC connection is not configured")
    if body.mode == "full" and body.confirm != svc.FULL_CONFIRM:
        raise HTTPException(status_code=422,
                            detail=f'full reload requires confirm="{svc.FULL_CONFIRM}"')
    dsn = svc._pg_dsn()
    try:
        # gate + insert the running row synchronously (fast, DB-only)…
        run_id = svc.start_run(body.mode, uuid.UUID(user["sub"]),
                               fetch=reader.fetch_nc, pg_dsn=dsn, run_worker=False)
    except svc.SyncAlreadyRunning as e:
        raise HTTPException(status_code=409, detail=str(e))
    # …then do the actual NC read + load on a worker thread.
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, svc._run_worker, run_id, body.mode, reader.fetch_nc, dsn)
    return {"run_id": str(run_id)}
