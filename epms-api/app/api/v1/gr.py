"""Goods Receipt endpoints."""
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select

from uniops_authz import has_permission

from app.core.authz import require_permission
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.core.access_scope import build_scope
from app.crud import gr as gr_crud
from app.crud import gr_report as gr_report_crud
from app.crud import po as po_crud
from app.models.task import Task
from app.schemas.gr import GrActionRequest, GrCreate, GrListResponse, GrResponse, is_service
from app.schemas.gr_report import (
    ReceivingReportResponse,
    ReceivingReportRow,
    ReceivingReportSummary,
)
from app.services.notification import fire_and_forget_notify
from app.services.receiving_report_xlsx import build_receiving_workbook

router = APIRouter(prefix="/gr", tags=["goods-receipts"])

WarehouseDep = Annotated[dict, Depends(require_permission("epms.gr.receive"))]


@router.get("", response_model=GrListResponse)
async def list_grs(
    db: SessionDep,
    user: CurrentUserPayload,
    status: str | None = Query(default=None),
    po_id: uuid.UUID | None = Query(default=None),
    vendor_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
    gr_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, le=200),
):
    scope = await build_scope(db, user)
    # Access Control Matrix gate — return empty when view_gr is disabled.
    if not scope["perms"].get("view_gr", False):
        return GrListResponse(items=[], total=0)
    items, total = await gr_crud.get_all(
        db, status=status, po_id=po_id, vendor_id=vendor_id,
        search=search, gr_type=gr_type,
        po_ids_subq=scope["po_subq"],
        page=page, page_size=page_size,
    )
    return GrListResponse(items=items, total=total)


@router.post("", response_model=GrResponse, status_code=201)
async def create_gr(body: GrCreate, db: SessionDep, user: CurrentUserPayload, token: BearerToken):
    po = await po_crud.get_by_id(db, body.po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    is_service_po = is_service(po.type)   # Service (4) and Project (6) follow service GR flow
    # Warehouse admission is the `epms.gr.receive` matrix permission — the same
    # gate as every other warehouse GR action (acknowledge/collect/confirm), so
    # it honors ADDITIONAL roles from identity's user_roles (a user whose JWT
    # primary role is e.g. `requester` but carries warehouse_staff via Portal
    # Admin must be admitted). system_admin short-circuits inside.
    can_receive = await has_permission(
        db, uuid.UUID(user["sub"]), user.get("role", ""), "epms.gr.receive")

    # Service/Project POs (type 4, 6): requester can confirm delivery; PO must be approved+
    # Physical POs: warehouse permission only; PO must be issued/partially_received
    #
    # "Requester" here means the creator of the PR linked to this PO — NOT a generic
    # 'requester' JWT role, and NOT the PO creator (POs are raised by procurement
    # staff). A service/project PO with no linked PR has no requester, so only
    # the warehouse permission admits its GR.
    if is_service_po:
        pr_requester_id = await gr_crud.get_pr_requester_id(db, po.pr_id)
        is_pr_requester = pr_requester_id is not None and pr_requester_id == uuid.UUID(user["sub"])
        if not can_receive and not is_pr_requester:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        if po.status not in ("approved", "issued", "partially_received"):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot create GR for PO in status '{po.status}'"
            )
    else:
        if not can_receive:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        if po.status not in ("issued", "partially_received"):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot create GR for PO in status '{po.status}' (must be issued or partially_received)"
            )

    gr = await gr_crud.create(db, body, po=po, created_by=uuid.UUID(user["sub"]), token=token)
    # Notify requester to acknowledge (acknowledge_gr task)
    new_tasks_result = await db.execute(
        select(Task).where(
            Task.document_type == "gr",
            Task.document_id == gr.id,
            Task.is_completed.is_(False),
        )
    )
    for task in new_tasks_result.scalars().all():
        fire_and_forget_notify(task, db)
    return gr


# ── Warehouse receiving report ────────────────────────────────────────────────
#
# Registered above /{gr_id}: that route's path parameter is a UUID, so a request
# for /gr/receiving-report would be rejected as a malformed id rather than
# falling through to these.


async def _receiving_rows(db, user, *, date_from, date_to, department_id, vendor_id,
                          search, page=None, page_size=None):
    """Shared body of the JSON and xlsx endpoints — same rows, same gate.

    Visibility is the GR list's, not a looser one: a report is a different
    rendering of records the caller can already open, never a way around the
    Access Control Matrix.
    """
    scope = await build_scope(db, user)
    if not scope["perms"].get("view_gr", False):
        return [], 0, gr_report_crud.ReceivingSummary(0, 0, 0, None), False
    return await gr_report_crud.receiving_rows(
        db,
        date_from=date_from, date_to=date_to,
        department_id=department_id, vendor_id=vendor_id, search=search,
        po_ids_subq=scope["po_subq"],
        page=page, page_size=page_size,
    )


@router.get("/receiving-report", response_model=ReceivingReportResponse)
async def receiving_report(
    db: SessionDep,
    user: CurrentUserPayload,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    department_id: uuid.UUID | None = Query(default=None),
    vendor_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    """One page of the window's received lines, with the window's own totals."""
    rows, total, summary, truncated = await _receiving_rows(
        db, user, date_from=date_from, date_to=date_to,
        department_id=department_id, vendor_id=vendor_id, search=search,
        page=page, page_size=page_size,
    )
    return ReceivingReportResponse(
        items=[ReceivingReportRow.model_validate(r) for r in rows],
        total=total,
        summary=ReceivingReportSummary.model_validate(summary),
        truncated=truncated,
    )


@router.get("/receiving-report/export")
async def export_receiving_report(
    db: SessionDep,
    user: CurrentUserPayload,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    department_id: uuid.UUID | None = Query(default=None),
    vendor_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
):
    """The whole window as an xlsx download — never just the page on screen."""
    rows, _total, _summary, _truncated = await _receiving_rows(
        db, user, date_from=date_from, date_to=date_to,
        department_id=department_id, vendor_id=vendor_id, search=search,
    )
    content = build_receiving_workbook(rows, date_from=date_from, date_to=date_to)
    filename = f"receiving-report-{date.today()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Without this a cross-origin browser download cannot read the name
            # above and falls back to whatever the caller guessed.
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.get("/{gr_id}", response_model=GrResponse)
async def get_gr(gr_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    from app.core.access_scope import is_gr_visible
    gr = await gr_crud.get_by_id(db, gr_id)
    if gr is None:
        raise HTTPException(status_code=404, detail="GR not found")
    scope = await build_scope(db, user)
    if not await is_gr_visible(db, gr, scope):
        raise HTTPException(status_code=404, detail="GR not found")
    return gr


@router.post("/{gr_id}/action", response_model=GrResponse)
async def gr_action(
    gr_id: uuid.UUID,
    body: GrActionRequest,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    gr = await gr_crud.get_by_id(db, gr_id)
    if gr is None:
        raise HTTPException(status_code=404, detail="GR not found")
    try:
        result = await gr_crud.action(db, gr, body, actor_id=uuid.UUID(user["sub"]), token=token)
        # Notify recipients for any newly created open tasks on this GR
        new_tasks_result = await db.execute(
            select(Task).where(
                Task.document_type == "gr",
                Task.document_id == gr_id,
                Task.is_completed.is_(False),
            )
        )
        for task in new_tasks_result.scalars().all():
            fire_and_forget_notify(task, db)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
