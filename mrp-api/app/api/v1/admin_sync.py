"""Manual trigger for the WMS inventory lot sync (Task 8).

Gated `mrp.param.write` (seeded in Task 9; system_admin bypasses the gate
regardless, per uniops_authz.bind — see app/core/authz.py).
"""
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.services.wms_sync.service import run_wms_sync

router = APIRouter(prefix="/admin", tags=["admin-sync"])

WriteDep = Annotated[dict, Depends(require_permission("mrp.param.write"))]


@router.post("/wms-sync")
async def trigger_wms_sync(db: SessionDep, _: WriteDep) -> dict:
    return await run_wms_sync(db)
