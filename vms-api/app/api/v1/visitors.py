"""Visitor CRUD endpoints (PRD §6.5.1).

All endpoints open to any authenticated UniOps user. The PRD does not gate
visitor creation by role — anyone who can host a visit can register a
visitor.
"""
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.core.deps import CurrentUserPayload, SessionDep
from app.core.request_meta import load_request_meta
from app.crud import audit as audit_crud
from app.crud import visitor as visitor_crud
from app.schemas.visitor import (
    VisitorCreate,
    VisitorListResponse,
    VisitorResponse,
    VisitorUpdate,
)
from app.services import compliance as compliance_svc

router = APIRouter(prefix="/visitors", tags=["visitors"])


@router.get("", response_model=VisitorListResponse)
async def list_visitors(
    db: SessionDep,
    _: CurrentUserPayload,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    rows, total = await visitor_crud.list_visitors(
        db, search=search, page=page, page_size=page_size
    )
    return VisitorListResponse(
        items=[VisitorResponse.model_validate(r) for r in rows],
        total=total,
    )


@router.get("/{visitor_id}", response_model=VisitorResponse)
async def get_visitor(
    visitor_id: uuid.UUID,
    db: SessionDep,
    _: CurrentUserPayload,
):
    row = await visitor_crud.get_visitor(db, visitor_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Visitor not found"
        )
    return VisitorResponse.model_validate(row)


@router.post("", response_model=VisitorResponse, status_code=status.HTTP_201_CREATED)
async def create_visitor(
    payload: VisitorCreate,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
    meta = await load_request_meta(db, user, request)
    row = await visitor_crud.create_visitor(db, payload)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="visitor.create",
        entity_type="visitor",
        entity_id=row.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        new_value=audit_crud.snapshot(row),
    )
    return VisitorResponse.model_validate(row)


@router.post("/{visitor_id}/confirm-training", response_model=VisitorResponse)
async def confirm_training(
    visitor_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """Mark the visitor's safety-training record as freshly confirmed.

    Authorization: the configured HR contact email (Admin panel) or any
    system_admin user. The compliance service checks identity; we 403
    here without leaking who's allowed.
    """
    row = await visitor_crud.get_visitor(db, visitor_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Visitor not found")

    meta = await load_request_meta(db, user, request)
    if not await compliance_svc.is_authorized_confirmer(
        db, kind="training", user_id=meta.user_id, user_role=meta.role,
    ):
        raise HTTPException(
            status_code=403,
            detail="Only the configured HR training contact can confirm training",
        )

    before = audit_crud.snapshot(row)
    row = await compliance_svc.confirm_training(db, row, confirmed_by=meta.user_id)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id, user_name=meta.user_name,
        action_type="visitor.confirm_training",
        entity_type="visitor", entity_id=row.id,
        ip_address=meta.ip_address, user_agent=meta.user_agent,
        old_value=before, new_value=audit_crud.snapshot(row),
    )
    return VisitorResponse.model_validate(row)


@router.post("/{visitor_id}/confirm-ppe", response_model=VisitorResponse)
async def confirm_ppe(
    visitor_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """Mark the visitor's PPE issuance as freshly recorded.

    Authorization: the configured Janitor PPE contact email (Admin panel)
    or any system_admin user.
    """
    row = await visitor_crud.get_visitor(db, visitor_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Visitor not found")

    meta = await load_request_meta(db, user, request)
    if not await compliance_svc.is_authorized_confirmer(
        db, kind="ppe", user_id=meta.user_id, user_role=meta.role,
    ):
        raise HTTPException(
            status_code=403,
            detail="Only the configured PPE contact can confirm PPE issuance",
        )

    before = audit_crud.snapshot(row)
    row = await compliance_svc.confirm_ppe(db, row, confirmed_by=meta.user_id)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id, user_name=meta.user_name,
        action_type="visitor.confirm_ppe",
        entity_type="visitor", entity_id=row.id,
        ip_address=meta.ip_address, user_agent=meta.user_agent,
        old_value=before, new_value=audit_crud.snapshot(row),
    )
    return VisitorResponse.model_validate(row)


@router.patch("/{visitor_id}", response_model=VisitorResponse)
async def patch_visitor(
    visitor_id: uuid.UUID,
    payload: VisitorUpdate,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
    row = await visitor_crud.get_visitor(db, visitor_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Visitor not found"
        )

    meta = await load_request_meta(db, user, request)
    before = audit_crud.snapshot(row)
    row = await visitor_crud.update_visitor(db, row, payload)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="visitor.update",
        entity_type="visitor",
        entity_id=row.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value=before,
        new_value=audit_crud.snapshot(row),
    )
    return VisitorResponse.model_validate(row)
