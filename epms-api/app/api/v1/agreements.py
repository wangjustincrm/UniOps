"""Purchase Agreement endpoints."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.authz import require_permission
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.crud import agreement as agr_crud
from app.crud import vendor as vendor_crud
from app.schemas.agreement import (
    AgreementActionRequest,
    AgreementCreate,
    AgreementListResponse,
    AgreementResponse,
    AgreementUpdate,
)
from app.services.approval_client import delegate_action

router = APIRouter(prefix="/agreements", tags=["purchase-agreements"])

AgrReadDep = Annotated[dict, Depends(require_permission("epms.agreement.read"))]
AgrWriteDep = Annotated[dict, Depends(require_permission("epms.agreement.write"))]


@router.get("", response_model=AgreementListResponse)
async def list_agreements(
    db: SessionDep,
    user: AgrReadDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    vendor_id: uuid.UUID | None = Query(default=None),
    agreement_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, le=200),
):
    items, total = await agr_crud.get_all(
        db, status=status_filter, vendor_id=vendor_id,
        agreement_type=agreement_type, search=search,
        page=page, page_size=page_size,
    )
    return {"items": items, "total": total}


@router.post("", response_model=AgreementResponse, status_code=status.HTTP_201_CREATED)
async def create_agreement(body: AgreementCreate, db: SessionDep, user: AgrWriteDep):
    vendor = await vendor_crud.get_by_id(db, body.vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return await agr_crud.create(
        db, body, vendor_name=vendor.name, created_by=uuid.UUID(user["sub"]),
    )


@router.get("/{agreement_id}", response_model=AgreementResponse)
async def get_agreement(agreement_id: uuid.UUID, db: SessionDep, user: AgrReadDep):
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return agr


@router.patch("/{agreement_id}", response_model=AgreementResponse)
async def update_agreement(
    agreement_id: uuid.UUID, body: AgreementUpdate, db: SessionDep, user: AgrWriteDep
):
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if agr.status not in agr_crud.EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Agreement is {agr.status}; only a draft agreement can be edited. "
                   "Changing terms after approval requires a new approval round.",
        )
    return await agr_crud.update(db, agr, body)


@router.post("/{agreement_id}/action", response_model=AgreementResponse)
async def agreement_action(
    agreement_id: uuid.UUID,
    body: AgreementActionRequest,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    # Gated by the open approval task, not by epms.agreement.write — approval
    # authority comes from the approval engine, mirroring po.py::po_action.
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    try:
        await delegate_action("agr", str(agreement_id), body.action, body.comment, token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    await db.refresh(agr)
    return agr
