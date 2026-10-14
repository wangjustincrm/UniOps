"""NC mirror freshness, manual refresh, and the tie-out findings.

`GET /admin/nc-sync/status` is gated inventory.view: anyone who can read the
numbers must be able to see how old they are and whether they tie out to NC.
`POST /admin/nc-sync` is gated inventory.admin.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.v1.perms import AdminDep, ViewDep, can_view_cost
from app.core.deps import SessionDep
from app.models import InvSyncState, InvTieoutIssue
from app.services import params
from app.services.nc_sync.lock import is_sync_running, nc_sync_lock
from app.services.nc_sync.service import SOURCE, nc_configured, run_nc_sync
from app.services.wms_sync import service as wms

router = APIRouter(prefix="/admin", tags=["admin-sync"])


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


async def _status(db, source: str, configured: bool) -> dict:
    state = await db.get(InvSyncState, source)
    interval = await params.get_param(db, params.NC_SYNC_INTERVAL_KEY)
    last_attempt = state.last_synced_at if state else None
    next_due_at = None
    if interval > 0 and last_attempt is not None:
        if last_attempt.tzinfo is None:
            last_attempt = last_attempt.replace(tzinfo=timezone.utc)
        next_due_at = last_attempt + timedelta(minutes=interval)
    return {
        "configured": configured,
        "interval_minutes": interval,
        "status": state.status if state else None,
        "last_synced_at": _iso(state.last_synced_at if state else None),
        "last_success_at": _iso(state.last_success_at if state else None),
        "last_error": state.last_error if state else None,
        "row_counts": (state.row_counts if state else None) or {},
        "filtered_counts": (state.filtered_counts if state else None) or {},
        "duration_ms": state.duration_ms if state else None,
        "tieout_ok": state.tieout_ok if state else None,
        "tieout_issue_count": state.tieout_issue_count if state else 0,
        "next_due_at": _iso(next_due_at),
        "running": await is_sync_running(db),
    }


@router.get("/nc-sync/status")
async def nc_sync_status(db: SessionDep, _: ViewDep) -> dict:
    return await _status(db, SOURCE, nc_configured())


@router.get("/wms-sync/status")
async def wms_sync_status(db: SessionDep, _: ViewDep) -> dict:
    """The WMS *transaction history* mirror owned by inventory-api — not
    mrp-api's current-stock mirror, which has its own status endpoint."""
    return await _status(db, wms.SOURCE, wms.wms_configured())


@router.get("/nc-sync/tieout-issues")
async def tieout_issues(
    db: SessionDep, payload: ViewDep,
    check: str | None = Query(None, max_length=40),
    limit: int = Query(200, ge=1, le=1000),
) -> dict:
    """All sources' tie-out findings. IA/GL findings measured in money are
    amounts like any other: without inventory.cost.view they are counted
    (`amount_issues_hidden`) but their rows are not returned (design §9)."""
    show_amt = await can_view_cost(payload, db)
    conds = []
    if check:
        conds.append(InvTieoutIssue.check_name == check)
    hidden = 0
    if not show_amt:
        hidden = (await db.execute(select(func.count()).select_from(InvTieoutIssue).where(
            InvTieoutIssue.measure == "amount", *conds))).scalar_one()
        conds.append(InvTieoutIssue.measure != "amount")
    rows = (await db.execute(select(InvTieoutIssue).where(*conds)
                             .order_by(InvTieoutIssue.check_name, InvTieoutIssue.key).limit(limit))).scalars().all()
    by_check = dict((await db.execute(
        select(InvTieoutIssue.check_name, func.count()).where(*conds)
        .group_by(InvTieoutIssue.check_name))).all())
    return {
        "total_stored": (await db.execute(select(func.count()).select_from(InvTieoutIssue).where(*conds))).scalar_one(),
        "amount_issues_hidden": hidden,
        "amounts_visible": show_amt,
        "by_check": by_check,
        "items": [{
            "check": r.check_name, "measure": r.measure, "key": r.key, "label": r.label,
            "mirror_value": str(r.mirror_value) if r.mirror_value is not None else None,
            "nc_value": str(r.nc_value) if r.nc_value is not None else None,
        } for r in rows],
    }


@router.post("/nc-sync")
async def trigger_nc_sync(db: SessionDep, _: AdminDep) -> dict:
    if not nc_configured():
        raise HTTPException(status_code=503, detail="NC connection is not configured")
    async with nc_sync_lock(db) as got:
        if not got:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="An NC sync is already running; wait for it to finish.")
        try:
            return await run_nc_sync(db)
        except Exception as exc:  # recorded on the state row by run_nc_sync
            raise HTTPException(status_code=502, detail=f"NC sync failed: {type(exc).__name__}") from exc


@router.post("/wms-sync")
async def trigger_wms_sync(db: SessionDep, _: AdminDep) -> dict:
    if not wms.wms_configured():
        raise HTTPException(status_code=503, detail="WMS connection is not configured")
    async with nc_sync_lock(db) as got:
        if not got:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="A sync is already running; wait for it to finish.")
        try:
            return await wms.run_wms_sync(db)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"WMS sync failed: {type(exc).__name__}") from exc
