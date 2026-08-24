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
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text

import re

from app.core.config import settings
from app.core.access_scope import _effective_role_codes
from app.core.deps import CurrentUserPayload, SessionDep, require_roles
from app.models.config import CompanyConfig
from app.models.nc_purchase_sync import RUNNING, NcPurchaseSyncRun
from app.services.nc_purchase_sync import reader
from app.services.nc_purchase_sync import service as svc
# The schedule the loop actually follows — its interval resolution and its
# bounds live there, so the endpoint cannot drift from what runs.
from app.tasks import nc_purchase_sync_scheduler as sched

router = APIRouter(prefix="/admin/nc-purchase-sync", tags=["nc-purchase-sync"])

# Setting the cutover is a company-wide decision — system_admin only (the
# incremental trigger itself is open to procurement_officer, see _SYNC_ROLES).
AdminOnlyDep = Annotated[dict, Depends(require_roles("system_admin"))]
_CUTOVER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$")


async def _effective_cutover(db) -> str:
    row = (await db.execute(
        select(CompanyConfig.nc_purchase_cutover).limit(1))).scalar_one_or_none()
    return row or getattr(settings, "nc_purchase_cutover", None) or svc._DEFAULT_CUTOVER

# Incremental sync may be triggered by a Procurement Officer (day-to-day), an
# ERP PA Officer (they live off the NC mirror — the PA queue for imported POs
# is only as fresh as the last sync, so making them wait for someone else to
# press the button is what made this a role question at all), or a system_admin.
# Full reload stays system_admin-only (enforced in trigger()).
# GET /status stays open to any authenticated user; it computes can_sync the
# same way this dependency does, so the button and the endpoint cannot disagree.
_SYNC_ROLES = frozenset(("system_admin", "procurement_officer", "erp_pa_officer"))


async def _may_sync(db, user: dict) -> bool:
    """Does this caller hold ANY sync-capable role — base OR granted?

    Must be the role UNION, not `payload["role"]`. `erp_pa_officer` is an
    ADDITIONAL role by design (see identity's user_roles): all three holders in
    production carry `requester` as their base role, so a `require_roles`-style
    check on the JWT claim alone would admit exactly nobody while the button
    happily rendered — the button appears, the click 403s.
    """
    return bool(_SYNC_ROLES & await _effective_role_codes(
        db, user.get("role") or "", uuid.UUID(user["sub"])))


async def _require_sync_role(user: CurrentUserPayload, db: SessionDep) -> dict:
    if not await _may_sync(db, user):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user


SyncDep = Annotated[dict, Depends(_require_sync_role)]


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
        "renamed_number_collision": r.renamed_number_collision,
        "skipped_number_collision": r.skipped_number_collision,
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
    interval = sched.resolve_interval_minutes((await db.execute(
        select(CompanyConfig.nc_purchase_sync_interval_minutes).limit(1))
    ).scalar_one_or_none())
    # When the scheduler will next pick it up. Measured from the last run's
    # START, the same way is_due() measures it — a countdown computed from
    # anything else would disagree with the loop it is describing. Null when
    # the schedule is off, so the UI says "off" rather than drawing a
    # countdown that never arrives.
    anchor = (current or last)
    next_due_at = None
    if interval > 0 and anchor is not None and anchor.started_at is not None:
        started = anchor.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        next_due_at = (started + timedelta(minutes=interval)).isoformat()
    return {
        "configured": svc.nc_configured(),
        "can_sync": await _may_sync(db, user),
        "can_set_cutover": user.get("role") == "system_admin",
        "cutover": await _effective_cutover(db),
        "interval_minutes": interval,
        "next_due_at": next_due_at,
        "current_run": _run_out(current),
        "last_run": _run_out(last),
    }


class IntervalIn(BaseModel):
    minutes: int


@router.patch("/interval")
async def set_interval(body: IntervalIn, db: SessionDep, user: AdminOnlyDep):
    """How often the sync runs itself. 0 turns the schedule off, leaving the
    button as the only trigger — which is the state that let the mirror go
    weeks without anybody noticing, so it is a choice rather than a default."""
    if isinstance(body.minutes, bool) or not 0 <= body.minutes <= sched.MAX_INTERVAL_MINUTES:
        raise HTTPException(
            status_code=422,
            detail=f"minutes must be between 0 and {sched.MAX_INTERVAL_MINUTES} "
                   f"(0 disables automatic sync)")
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalars().first()
    if cfg is None:
        raise HTTPException(status_code=404, detail="company config not found")
    cfg.nc_purchase_sync_interval_minutes = body.minutes
    await db.commit()
    return {"interval_minutes": body.minutes}


class CutoverIn(BaseModel):
    cutover: str


@router.patch("/cutover")
async def set_cutover(body: CutoverIn, db: SessionDep, user: AdminOnlyDep):
    val = body.cutover.strip()
    if not _CUTOVER_RE.match(val):
        raise HTTPException(status_code=422,
                            detail="cutover must be 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'")
    if len(val) == 10:
        val += " 00:00:00"   # date-only -> start of day
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalars().first()
    if cfg is None:
        raise HTTPException(status_code=404, detail="company config not found")
    cfg.nc_purchase_cutover = val
    await db.commit()
    return {"cutover": val}


@router.post("", status_code=202)
async def trigger(body: SyncIn, user: SyncDep):
    if not svc.nc_configured():
        raise HTTPException(status_code=503, detail="NC connection is not configured")
    if body.mode == "full":
        # Full reload is destructive — restrict to system_admin.
        if user.get("role") != "system_admin":
            raise HTTPException(status_code=403, detail="full reload is system_admin only")
        if body.confirm != svc.FULL_CONFIRM:
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
