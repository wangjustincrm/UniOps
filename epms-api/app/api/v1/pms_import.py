"""Admin endpoints for the legacy-PMS → EPMS import (manual trigger only).

All routes require the ``admin_panel`` permission (Access Control Matrix).
The actual work runs in a background task (see services.pms_import_runner);
the UI polls /status and /runs.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.config import settings
from app.core.deps import CurrentUserPayload, require_permission
from app.services import pms_import_runner as runner

router = APIRouter(prefix="/pms-import", tags=["pms-import"])

AdminDep = Annotated[dict, Depends(require_permission("admin_panel"))]


class RunRequest(BaseModel):
    # full = import everything (new docs only); incremental = sync docs changed
    # since the last committed sync (header-level update + new docs).
    phase: Literal["full", "incremental"] = "incremental"
    dry_run: bool = True


@router.get("/config")
async def get_config(_: AdminDep) -> dict:
    """Surface SharePoint config (no secrets) + last incremental-sync watermark."""
    from scripts.import_pms import state
    return {
        "sharepoint_configured": bool(settings.SP_USER and settings.SP_PASSWORD),
        "site": settings.SP_SITE,
        "user": settings.SP_USER or None,
        "last_sync": state.get_last_sync(),
    }


@router.get("/status")
async def get_status(_: AdminDep) -> dict:
    return {"current": runner.current_run()}


@router.get("/runs")
async def list_runs(_: AdminDep) -> dict:
    return {"runs": runner.list_runs()}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, _: AdminDep) -> dict:
    run = runner.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def start_run(body: RunRequest, payload: CurrentUserPayload, _: AdminDep) -> dict:
    triggered_by = payload.get("email") or payload.get("sub") or "admin"
    try:
        return runner.start_run(body.phase, body.dry_run, triggered_by)
    except runner.SyncBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
