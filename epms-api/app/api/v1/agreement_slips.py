"""House-account pickup slip endpoints (entry / list / void / AP review)."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.api.v1.invoices import ApDep
from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.crud import agreement as agr_crud
from app.crud import agreement_slip as slip_crud
from app.models.agreement_slip import AgreementPickupSlip
from app.schemas.agreement_slip import (
    SlipApReview,
    SlipCreate,
    SlipListResponse,
    SlipResponse,
    SlipUpdate,
)

router = APIRouter(prefix="/agreements/{agreement_id}/slips", tags=["agreement-pickup-slips"])

SlipReadDep = Annotated[dict, Depends(require_permission("epms.agreement.read"))]
SlipWriteDep = Annotated[dict, Depends(require_permission("epms.agreement.write"))]


async def _get_agreement_or_404(db: SessionDep, agreement_id: uuid.UUID):
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return agr


async def _get_slip_or_404(
    db: SessionDep, agreement_id: uuid.UUID, slip_id: uuid.UUID,
) -> AgreementPickupSlip:
    slip = (await db.execute(
        select(AgreementPickupSlip).where(
            AgreementPickupSlip.id == slip_id,
            AgreementPickupSlip.agreement_id == agreement_id,
        )
    )).scalar_one_or_none()
    if slip is None:
        raise HTTPException(status_code=404, detail="Slip not found")
    return slip


@router.get("", response_model=SlipListResponse)
async def list_slips(
    agreement_id: uuid.UUID,
    db: SessionDep,
    user: SlipReadDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
):
    await _get_agreement_or_404(db, agreement_id)
    items = await slip_crud.list_for_agreement(db, agreement_id, status=status_filter)
    return {"items": items, "total": len(items)}


@router.post("", response_model=SlipResponse, status_code=status.HTTP_201_CREATED)
async def create_slip(
    agreement_id: uuid.UUID, body: SlipCreate, db: SessionDep, user: SlipWriteDep,
):
    agr = await _get_agreement_or_404(db, agreement_id)
    return await slip_crud.create(db, agr, body, created_by=uuid.UUID(user["sub"]))


@router.patch("/{slip_id}", response_model=SlipResponse)
async def update_slip(
    agreement_id: uuid.UUID, slip_id: uuid.UUID, body: SlipUpdate,
    db: SessionDep, user: SlipWriteDep,
):
    await _get_agreement_or_404(db, agreement_id)
    slip = await _get_slip_or_404(db, agreement_id, slip_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(slip, field, value)
    await db.flush()
    return slip


@router.delete("/{slip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def void_slip(
    agreement_id: uuid.UUID, slip_id: uuid.UUID, db: SessionDep, user: SlipWriteDep,
):
    await _get_agreement_or_404(db, agreement_id)
    slip = await _get_slip_or_404(db, agreement_id, slip_id)
    try:
        await slip_crud.void(db, slip)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{slip_id}/ap-review", response_model=SlipResponse)
async def ap_review_slip(
    agreement_id: uuid.UUID, slip_id: uuid.UUID, body: SlipApReview,
    db: SessionDep, user: ApDep,
):
    await _get_agreement_or_404(db, agreement_id)
    slip = await _get_slip_or_404(db, agreement_id, slip_id)
    try:
        return await slip_crud.ap_review(db, slip, body.action, uuid.UUID(user["sub"]))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
