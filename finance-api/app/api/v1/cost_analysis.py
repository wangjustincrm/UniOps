"""AI Financial Analysis — read API + the NC cost-mirror sync.

Reads are gated on `finance.analysis.view` (identity migration
0016_fin_analysis_perm), NOT view_finance: unit costs and margins are not for
everyone who can open the AP list. Writes (sync trigger, schedule, thresholds)
are system_admin only, like the other NC syncs.

No notifications, no tasks (decision D2): people come here to look.
"""
import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.nc_cost import RUNNING, FaConfig, NcCostSyncRun
from app.services import cost_analysis as ca
from app.services import cost_diagnosis as dx
from app.services import cost_batch as bt
from app.services import cost_margin as mg
from app.services import cost_standard as sc
from app.services import nc_cost_sync as svc
from app.tasks import nc_cost_sync_scheduler as sched

VIEW = "finance.analysis.view"

router = APIRouter(prefix="/cost-analysis", tags=["cost-analysis"])
_can_view = require_permission(VIEW)

# test seams
_worker_dsn = svc._pg_dsn
_fetch = svc.fetch_from_nc

_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _period(v: str, name: str) -> str:
    if not _PERIOD.match(v or ""):
        raise HTTPException(status_code=422, detail=f"{name} must look like 2026-08")
    return v


def _range(period_from: str, period_to: str) -> tuple[str, str]:
    a, b = _period(period_from, "from"), _period(period_to, "to")
    if a > b:
        raise HTTPException(status_code=422, detail="from must not be after to")
    return a, b


def _run_out(r: NcCostSyncRun | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": str(r.id), "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "counts": r.counts, "tie_out_ok": r.tie_out_ok,
        "tie_out_diffs": (r.tie_out or {}).get("diffs") if r.tie_out else None,
        "error": r.error,
    }


@router.get("/status")
async def status(user: dict = Depends(_can_view), db: AsyncSession = Depends(get_db)):
    await db.execute(text(
        "update nc_cost_sync_runs set status = 'failed', error = 'abandoned', "
        "finished_at = now(), updated_at = now() "
        "where status = 'running' and started_at < :cutoff"),
        {"cutoff": datetime.now(timezone.utc) - timedelta(minutes=30)})
    await db.commit()
    current = (await db.execute(
        select(NcCostSyncRun).where(NcCostSyncRun.status == RUNNING)
        .order_by(NcCostSyncRun.started_at.desc()).limit(1))).scalars().first()
    last = (await db.execute(
        select(NcCostSyncRun).where(NcCostSyncRun.status != RUNNING)
        .order_by(NcCostSyncRun.started_at.desc()).limit(1))).scalars().first()
    last_ok = (await db.execute(
        select(NcCostSyncRun).where(NcCostSyncRun.status == "success")
        .order_by(NcCostSyncRun.started_at.desc()).limit(1))).scalars().first()
    cfg = (await db.execute(select(FaConfig).where(FaConfig.id == 1))).scalars().first()
    return {
        "can_sync": user.get("role") == "system_admin",
        "configured": svc.nc_configured(),
        "scope_from": svc.PERIOD_FLOOR,
        "current_run": _run_out(current),
        "last_run": _run_out(last),
        "last_success": _run_out(last_ok),
        "interval_minutes": sched.resolve_interval_minutes(
            cfg.sync_interval_minutes if cfg else None),
        "thresholds": await ca.load_thresholds(db),
    }


@router.post("/sync", status_code=202)
async def trigger(user: CurrentUser):
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
    if not svc.nc_configured():
        raise HTTPException(status_code=503, detail="NC connection is not configured")
    dsn = _worker_dsn()
    try:
        run_id = svc.start_run(uuid.UUID(user["sub"]), fetch=_fetch, pg_dsn=dsn,
                               run_worker=False)
    except svc.SyncAlreadyRunning as e:
        raise HTTPException(status_code=409, detail=str(e))
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, svc._run_worker, run_id, _fetch, dsn)
    return {"run_id": str(run_id)}


@router.get("/runs")
async def runs(limit: int = 20, user: dict = Depends(_can_view),
               db: AsyncSession = Depends(get_db)):
    limit = max(1, min(limit, 100))
    rows = (await db.execute(
        select(NcCostSyncRun).order_by(NcCostSyncRun.started_at.desc()).limit(limit)
    )).scalars().all()
    return {"runs": [_run_out(r) for r in rows]}


class ConfigIn(BaseModel):
    interval_minutes: int | None = None
    thresholds: dict[str, float] | None = None


@router.patch("/config")
async def set_config(body: ConfigIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
    cfg = (await db.execute(select(FaConfig).where(FaConfig.id == 1))).scalars().first()
    if cfg is None:
        raise HTTPException(status_code=404, detail="fa_config row missing")
    if body.interval_minutes is not None:
        if not 0 <= body.interval_minutes <= sched.MAX_INTERVAL_MINUTES:
            raise HTTPException(status_code=422, detail=(
                f"interval_minutes must be between 0 and {sched.MAX_INTERVAL_MINUTES} "
                f"(0 disables automatic sync)"))
        cfg.sync_interval_minutes = body.interval_minutes
    if body.thresholds is not None:
        unknown = set(body.thresholds) - set(ca.DEFAULT_THRESHOLDS)
        if unknown:
            raise HTTPException(status_code=422,
                                detail=f"unknown thresholds: {', '.join(sorted(unknown))}")
        if any(v < 0 for v in body.thresholds.values()):
            raise HTTPException(status_code=422, detail="thresholds must not be negative")
        cfg.thresholds = {**(cfg.thresholds or {}), **body.thresholds}
    cfg.updated_by = uuid.UUID(user["sub"])
    cfg.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"interval_minutes": sched.resolve_interval_minutes(cfg.sync_interval_minutes),
            "thresholds": await ca.load_thresholds(db)}


@router.get("/materials")
async def materials(q: str | None = None, limit: int = Query(50, ge=1, le=200),
                    user: dict = Depends(_can_view), db: AsyncSession = Depends(get_db)):
    return {"materials": await ca.materials(db, q, limit)}


@router.get("/trace")
async def trace(material: str, period_from: str = Query(..., alias="from"),
                period_to: str = Query(..., alias="to"),
                user: dict = Depends(_can_view), db: AsyncSession = Depends(get_db)):
    a, b = _range(period_from, period_to)
    material = material.strip()
    if not material:
        raise HTTPException(status_code=422, detail="material is required")
    out = await ca.material_trace(db, material, a, b)
    # The conclusion first, the data as its evidence (user, 2026-10-09).
    out["diagnosis"] = await dx.diagnose(db, material, a, b, trace=out)
    return out


@router.get("/margin")
async def margin(period_from: str = Query(..., alias="from"),
                 period_to: str = Query(..., alias="to"),
                 user: dict = Depends(_can_view), db: AsyncSession = Depends(get_db)):
    a, b = _range(period_from, period_to)
    matrix = await mg.margin_matrix(db, a, b)
    return {**matrix, "summary": await mg.margin_summary(db, matrix)}


@router.get("/chain")
async def chain(material: str, period: str, user: dict = Depends(_can_view),
                db: AsyncSession = Depends(get_db)):
    material = material.strip().upper()
    if not material:
        raise HTTPException(status_code=422, detail="material is required")
    return await mg.cost_chain(db, material, _period(period, "period"))


@router.get("/batches")
async def batches(material: str, period_from: str = Query(..., alias="from"),
                  period_to: str = Query(..., alias="to"),
                  user: dict = Depends(_can_view), db: AsyncSession = Depends(get_db)):
    a, b = _range(period_from, period_to)
    return await bt.batches(db, material.strip().upper(), a, b)


@router.get("/batch")
async def batch(material: str, batch: str, user: dict = Depends(_can_view),
                db: AsyncSession = Depends(get_db)):
    tree = await bt.batch_cost(db, material.strip().upper(), batch.strip())
    if tree is None:
        raise HTTPException(status_code=404, detail="no completion receipt for that batch")
    return {"batch": tree, "summary": bt.summarise(tree)}


@router.get("/standard-cost")
async def standard_cost(material: str, period_from: str = Query(..., alias="from"),
                        period_to: str = Query(..., alias="to"),
                        window_months: int = Query(6, ge=1, le=12),
                        mad_k: float = Query(3.0, gt=0, le=10),
                        bom_tolerance: float = Query(0.05, ge=0, le=1),
                        exclude_stoppage: bool = True,
                        user: dict = Depends(_can_view), db: AsyncSession = Depends(get_db)):
    """A suggestion only — nothing is written, NC keeps costing actuals."""
    a, b = _range(period_from, period_to)
    material = material.strip().upper()
    if not material:
        raise HTTPException(status_code=422, detail="material is required")
    return await sc.standard_cost(db, material, a, b, {
        "window_months": window_months, "mad_k": mad_k, "bom_tolerance": bom_tolerance,
        "exclude_stoppage": exclude_stoppage})


@router.get("/diagnosis")
async def diagnosis(material: str, period_from: str = Query(..., alias="from"),
                    period_to: str = Query(..., alias="to"),
                    user: dict = Depends(_can_view), db: AsyncSession = Depends(get_db)):
    a, b = _range(period_from, period_to)
    material = material.strip().upper()
    if not material:
        raise HTTPException(status_code=422, detail="material is required")
    return await dx.diagnose(db, material, a, b)


@router.get("/anomalies")
async def anomalies(period_from: str = Query(..., alias="from"),
                    period_to: str = Query(..., alias="to"),
                    user: dict = Depends(_can_view), db: AsyncSession = Depends(get_db)):
    a, b = _range(period_from, period_to)
    return await ca.anomalies(db, a, b)
