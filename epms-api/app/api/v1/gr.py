"""Goods Receipt endpoints."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import BearerToken, CurrentUserPayload, SessionDep, require_roles
from app.core.access_scope import build_scope
from app.crud import gr as gr_crud
from app.crud import po as po_crud
from app.models.task import Task
from app.schemas.gr import GrActionRequest, GrCreate, GrListResponse, GrResponse
from app.services.notification import fire_and_forget_notify

router = APIRouter(prefix="/gr", tags=["goods-receipts"])

_WAREHOUSE_ROLES = ("system_admin", "warehouse_staff", "procurement_officer")
WarehouseDep = Annotated[dict, Depends(require_roles(*_WAREHOUSE_ROLES))]


@router.get("", response_model=GrListResponse)
async def list_grs(
    db: SessionDep,
    user: CurrentUserPayload,
    status: str | None = Query(default=None),
    po_id: uuid.UUID | None = Query(default=None),
    vendor_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, le=200),
):
    scope = await build_scope(db, user)
    # Access Control Matrix gate — return empty when view_gr is disabled.
    if not scope["perms"].get("view_gr", False):
        return GrListResponse(items=[], total=0)
    items, total = await gr_crud.get_all(
        db, status=status, po_id=po_id, vendor_id=vendor_id,
        po_ids_subq=scope["po_subq"],
        page=page, page_size=page_size,
    )
    return GrListResponse(items=items, total=total)


@router.post("", response_model=GrResponse, status_code=201)
async def create_gr(body: GrCreate, db: SessionDep, user: CurrentUserPayload, token: BearerToken):
    po = await po_crud.get_by_id(db, body.po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    role = user.get("role", "")
    is_service_po = po.type in (4, 6)   # Service (4) and Project (6) follow service GR flow
    warehouse_roles = {"system_admin", "warehouse_staff", "procurement_officer", "procurement_manager"}

    # Service/Project POs (type 4, 6): requester can confirm delivery; PO must be approved+
    # Physical POs: warehouse roles only; PO must be issued/partially_received
    #
    # "Requester" here means the creator of the PR linked to this PO — NOT a generic
    # 'requester' JWT role, and NOT the PO creator (POs are raised by procurement
    # staff). A service/project PO with no linked PR has no requester, so only
    # warehouse roles may create its GR.
    if is_service_po:
        pr_requester_id = await gr_crud.get_pr_requester_id(db, po.pr_id)
        is_pr_requester = pr_requester_id is not None and pr_requester_id == uuid.UUID(user["sub"])
        if role not in warehouse_roles and not is_pr_requester:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        if po.status not in ("approved", "issued", "partially_received"):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot create GR for PO in status '{po.status}'"
            )
    else:
        if role not in warehouse_roles:
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
