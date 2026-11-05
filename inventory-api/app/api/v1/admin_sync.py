"""NC mirror freshness, manual refresh, and the tie-out findings.

`GET /admin/nc-sync/status` is gated inventory.view: anyone who can read the
numbers must be able to see how old they are and whether they tie out to NC.
`POST /admin/nc-sync` is gated inventory.admin.
"""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, select

from app.api.v1.perms import AdminDep, ViewDep, can_view_cost
from app.core.deps import SessionDep
from app.core.list_sort import SortDir, resolve
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


# The page's names for each check, so `sort=check` orders by what the column
# shows. Mirrors TIEOUT_CHECK_LABELS in inventory/src/pages/settings/
# settingsApi.ts; an unknown check sorts by its raw name, as the page shows it.
_CHECK_LABELS = {
    "ic_flow_vs_onhand": "NC stock: flows vs on-hand",
    "ia_ledger_vs_monthnab": "NC cost: ledger vs month-end balance",
    "gl_lines_vs_balance": "NC GL: posted lines vs GL balance",
    wms.CHECK: "WMS: transactions vs current lots",
}
_I = InvTieoutIssue
_TIEOUT_SORT = {
    "check": func.lower(case(*[(_I.check_name == k, v) for k, v in _CHECK_LABELS.items()],
                             else_=_I.check_name)),
    "measure": _I.measure,
    "label": func.lower(func.coalesce(func.nullif(_I.label, ""), _I.key)),
    "mirror": _I.mirror_value,
    "nc": _I.nc_value,
    "diff": _I.mirror_value - _I.nc_value,
}


@router.get("/nc-sync/tieout-issues")
async def tieout_issues(
    db: SessionDep, payload: ViewDep,
    check: str | None = Query(None, max_length=40),
    source: Literal["nc", "wms"] | None = Query(
        None, description="Only one sync's checks: wms = the WMS check, nc = every other"),
    limit: int = Query(200, ge=1, le=1000, description="Legacy cap, used when page_size is absent"),
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=500),
    sort: str | None = Query(None),
    dir_: SortDir = Query("asc", alias="dir"),
) -> dict:
    """All sources' tie-out findings. IA/GL findings measured in money are
    amounts like any other: without inventory.cost.view they are counted
    (`amount_issues_hidden`) but their rows are not returned (design §9).

    With `page_size` the list is paged (and `sort`/`dir` sort it before the
    page is cut); without it, the first `limit` rows as before."""
    srt = resolve(sort, dir_, _TIEOUT_SORT)
    show_amt = await can_view_cost(payload, db)
    conds = []
    if check:
        conds.append(_I.check_name == check)
    if source == "wms":
        conds.append(_I.check_name == wms.CHECK)
    elif source == "nc":
        conds.append(_I.check_name != wms.CHECK)
    hidden = 0
    if not show_amt:
        hidden = (await db.execute(select(func.count()).select_from(_I).where(
            _I.measure == "amount", *conds))).scalar_one()
        conds.append(_I.measure != "amount")
    order = ([(_TIEOUT_SORT[srt[0]].desc() if srt[1] == "desc" else _TIEOUT_SORT[srt[0]].asc()).nulls_last(),
              _I.id] if srt
             else [_I.check_name, _I.key, _I.measure, _I.id])
    stmt = select(_I).where(*conds).order_by(*order)
    if page_size is None:
        stmt = stmt.limit(limit)
    else:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()
    by_check = dict((await db.execute(
        select(_I.check_name, func.count()).where(*conds)
        .group_by(_I.check_name))).all())
    total = (await db.execute(select(func.count()).select_from(_I).where(*conds))).scalar_one()
    return {
        "total_stored": total,
        "total": total, "page": page, "page_size": page_size,
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
