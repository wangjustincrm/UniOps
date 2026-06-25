"""Compliance report endpoints (W11+W12 S2-E / PRD §6.5.5 VMS-CR-001..002).

Auditor/admin-gated. Each report streams as CSV — F8 locked decision is
"interim format"; Excel opens these cleanly. Every export is recorded in
the audit log so the regulator chain is intact even when data leaves the
system.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.core.deps import SessionDep, require_roles
from app.core.request_meta import load_request_meta
from app.crud import audit as audit_crud
from app.services import reports as reports_svc

router = APIRouter(prefix="/reports", tags=["reports"])

# Auditor + admin only — matches the audit-log read scope.
ReportDep = Annotated[dict, Depends(require_roles("auditor", "system_admin"))]


async def _audit_export(
    db, request: Request, user_payload: dict,
    *, action_type: str, date_from: date, date_to: date, label: str,
) -> None:
    meta = await load_request_meta(db, user_payload, request)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type=action_type,
        entity_type="report",
        entity_id=meta.user_id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        new_value={"from": date_from.isoformat(), "to": date_to.isoformat()},
        notes=f"{label} {date_from}→{date_to}",
    )


def _csv_response(stream, filename: str) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/cfia-visit-log")
async def cfia_visit_log(
    request: Request,
    db: SessionDep,
    user: ReportDep,
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
):
    """16-column CFIA-style visit log for [from, to] inclusive."""
    await _audit_export(
        db, request, user,
        action_type="export_cfia_visit_log",
        date_from=date_from, date_to=date_to, label="CFIA visit log",
    )
    filename = f"vms-cfia-visit-log-{date_from}-{date_to}.csv"
    return _csv_response(
        reports_svc.stream_cfia_visit_log(db, date_from=date_from, date_to=date_to),
        filename,
    )


@router.get("/gmp-area-summary")
async def gmp_area_summary(
    request: Request,
    db: SessionDep,
    user: ReportDep,
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
):
    """One row per GMP/Lab area for the requested window."""
    await _audit_export(
        db, request, user,
        action_type="export_gmp_area_summary",
        date_from=date_from, date_to=date_to, label="GMP area summary",
    )
    filename = f"vms-gmp-area-summary-{date_from}-{date_to}.csv"
    return _csv_response(
        reports_svc.stream_gmp_area_summary(db, date_from=date_from, date_to=date_to),
        filename,
    )
