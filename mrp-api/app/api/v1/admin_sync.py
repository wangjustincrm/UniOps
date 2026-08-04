"""Manual trigger for the WMS inventory lot sync (Task 8).

Gated `mrp.param.write` (seeded in Task 9; system_admin bypasses the gate
regardless, per uniops_authz.bind — see app/core/authz.py).
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.services.wms_sync.service import run_wms_sync, wms_configured

router = APIRouter(prefix="/admin", tags=["admin-sync"])

WriteDep = Annotated[dict, Depends(require_permission("mrp.param.write"))]


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
    return await run_wms_sync(db)
