"""QuickBooks Online mirror API — sync trigger/status + browse. Auth via CurrentUser."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.qbo import RUNNING, QboSyncRun
from app.services import qbo_sync as svc

# Server-side auth only (CurrentUser); view_finance is the client-side nav gate.
router = APIRouter(prefix="/qbo", tags=["qbo"])


class SyncIn(BaseModel):
    mode: Literal["full", "incremental"]
    entities: list[str] | None = None
    with_attachments: bool = True
    confirm: str | None = None


def _run_out(r: QboSyncRun | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": str(r.id), "mode": r.mode, "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "counters": r.counters, "watermarks": r.watermarks, "error": r.error,
    }


@router.get("/sync/status")
async def sync_status(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await db.execute(text(
        "update qbo_sync_runs set status='failed', error='abandoned', "
        "finished_at=now(), updated_at=now() "
        "where status='running' and updated_at < :cutoff"),
        {"cutoff": datetime.now(timezone.utc) - svc.STALE_AFTER})
    await db.commit()
    current = (await db.execute(select(QboSyncRun).where(QboSyncRun.status == RUNNING)
               .order_by(QboSyncRun.started_at.desc()).limit(1))).scalars().first()
    last = (await db.execute(select(QboSyncRun).where(QboSyncRun.status != RUNNING)
            .order_by(QboSyncRun.started_at.desc()).limit(1))).scalars().first()
    return {"can_sync": True, "configured": svc.qbo_configured(),
            "current_run": _run_out(current), "last_run": _run_out(last)}


@router.get("/sync/runs")
async def sync_runs(user: CurrentUser, db: AsyncSession = Depends(get_db), limit: int = 20):
    rows = (await db.execute(select(QboSyncRun)
            .order_by(QboSyncRun.started_at.desc()).limit(min(limit, 100)))).scalars().all()
    return {"items": [_run_out(r) for r in rows]}


@router.post("/sync", status_code=202)
async def trigger_sync(body: SyncIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    if not svc.qbo_configured():
        raise HTTPException(status_code=503, detail="QBO connection is not configured")
    if body.mode == "full" and body.confirm != svc.FULL_CONFIRM:
        raise HTTPException(status_code=422, detail=f'full reload requires confirm="{svc.FULL_CONFIRM}"')
    running = (await db.execute(select(QboSyncRun).where(QboSyncRun.status == RUNNING).limit(1))).scalars().first()
    if running:
        raise HTTPException(status_code=409, detail="a sync is already running")
    svc.launch_sync(mode=body.mode, entities=body.entities, with_attachments=body.with_attachments)
    return {"status": "started"}
