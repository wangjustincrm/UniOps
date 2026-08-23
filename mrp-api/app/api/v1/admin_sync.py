"""The WMS mirror: how fresh it is, and how to refresh it now.

`GET /admin/wms-sync/status` — freshness and outcome of the last run, read by
both the Inventory page header and Portal -> Admin -> WMS Sync. Gated
`mrp.report.view`: everyone who can read the inventory numbers must be able to
see how old they are. A screen that shows stock without showing its age invites
somebody to plan against a three-week-old snapshot, which is precisely what
happened before this endpoint existed.

`POST /admin/wms-sync` — run one now. Gated `mrp.param.write` (seeded in Task
9; system_admin bypasses the gate regardless, per uniops_authz.bind — see
app/core/authz.py).

The automatic schedule is in app/services/wms_sync/scheduler.py; its interval
is the `wms_sync_interval_minutes` planning parameter, written through
`PUT /params/{key}` like every other planning parameter.
"""
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.params import (
    DEFAULT_WMS_SYNC_INTERVAL_MINUTES,
    WMS_SYNC_INTERVAL_KEY,
    get_param,
)
from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.models.sync_state import MrpSyncState
from app.services.wms_sync.lock import is_sync_running, wms_sync_lock
from app.services.wms_sync.scheduler import resolve_interval_minutes
from app.services.wms_sync.service import run_wms_sync, wms_configured

router = APIRouter(prefix="/admin", tags=["admin-sync"])

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]
WriteDep = Annotated[dict, Depends(require_permission("mrp.param.write"))]

_SOURCE = "wms"


def _iso(value: datetime | None) -> str | None:
    """Always hand the browser an absolute, timezone-carrying instant.

    The columns are `timestamptz`, but a driver can still return a naive
    datetime; serialising that would produce a string the browser reads as
    local time and render an "8 hours ago" that is off by the UTC offset.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


@router.get("/wms-sync/status")
async def wms_sync_status(db: SessionDep, _: ReadDep) -> dict:
    state = await db.get(MrpSyncState, _SOURCE)
    interval = resolve_interval_minutes(
        await get_param(db, WMS_SYNC_INTERVAL_KEY, DEFAULT_WMS_SYNC_INTERVAL_MINUTES))

    last_attempt = state.last_synced_at if state else None
    # When the next automatic run is expected. Null when automatic sync is off
    # (interval 0) — the UI must then say "manual only" rather than draw a
    # countdown that never arrives. Never-synced-yet means "as soon as the
    # scheduler next ticks", which is what a null last attempt + non-zero
    # interval renders as.
    next_due_at = None
    if interval > 0 and last_attempt is not None:
        if last_attempt.tzinfo is None:
            last_attempt = last_attempt.replace(tzinfo=timezone.utc)
        next_due_at = last_attempt + timedelta(minutes=interval)

    return {
        "configured": wms_configured(),
        "interval_minutes": interval,
        "status": state.status if state else None,
        # last_synced_at is the last ATTEMPT; last_success_at is how old the
        # data on screen is. They differ exactly when something is wrong, which
        # is when the distinction matters (see migration mrp16).
        "last_synced_at": _iso(state.last_synced_at if state else None),
        "last_success_at": _iso(state.last_success_at if state else None),
        "row_count": state.row_count if state else 0,
        "last_error": state.last_error if state else None,
        "next_due_at": _iso(next_due_at),
        "running": await is_sync_running(db),
    }


@router.post("/wms-sync")
async def trigger_wms_sync(db: SessionDep, _: WriteDep) -> dict:
    # wms_configured() is defined (see wms_sync/reader.py) but was never
    # enforced here — an unconfigured WMS connection (WMS_* left blank,
    # "feature hidden" per docker-compose.prod.yml) would previously fall
    # through to fetch_inventory() and raise an opaque oracledb/DSN error as
    # an unhandled 500. Guard it the same way epms-api/app/api/v1/
    # nc_purchase_sync.py and finance-api/app/api/v1/nc_coa_sync.py gate
    # their own NC sync triggers.
    if not wms_configured():
        raise HTTPException(status_code=503, detail="WMS connection is not configured")
    # Single-flight against the scheduler and against a second impatient click:
    # `run_wms_sync` empties the mirror before refilling it, so two overlapping
    # runs would blank each other's snapshot under the reader's feet.
    async with wms_sync_lock(db) as got:
        if not got:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A WMS sync is already running; wait for it to finish.",
            )
        return await run_wms_sync(db)
