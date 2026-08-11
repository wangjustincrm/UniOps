"""House-account pickup slip endpoints (entry / list / void / AP review)."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.v1.invoices import ApDep
from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.crud import agreement as agr_crud
from app.crud import agreement_slip as slip_crud
from app.models.agreement import PurchaseAgreement
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


async def _get_agreement_or_404(db: SessionDep, agreement_id: uuid.UUID) -> PurchaseAgreement:
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


async def _duplicate_ref_error(
    db: SessionDep, agr_number: str, slip_ref: str | None,
) -> HTTPException:
    # The partial unique index (agreement_id, slip_ref) WHERE slip_ref IS NOT
    # NULL is a real collision path — the same paper slip re-entered by a
    # second person, or a client retry after a timeout — not an edge case.
    # It surfaces as IntegrityError, which crud never raises as ValueError,
    # so it must be caught here rather than falling through to the generic
    # 500 the get_session dependency would otherwise let through.
    #
    # A failed flush leaves the session in "pending rollback", and even a
    # PLAIN attribute read on an already-loaded ORM object (e.g. agr.number)
    # tries to transparently re-fetch it — first raising PendingRollbackError
    # (session unusable), and after an explicit rollback(), MissingGreenlet
    # (the lazy-load's implicit IO has no async context to run in from a
    # synchronous f-string expression). That's why the caller passes the
    # agreement NUMBER as a plain str captured before the failing call,
    # instead of the ORM object — nothing here touches `agr` at all. The
    # rollback below is still required so the session is usable again for
    # anything downstream (e.g. get_session's own teardown).
    await db.rollback()
    return HTTPException(
        status_code=409,
        detail=f"Slip reference '{slip_ref}' is already recorded for "
               f"agreement {agr_number}.",
    )


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
    agr_number = agr.number   # capture before any flush that might fail — see _duplicate_ref_error
    try:
        return await slip_crud.create(db, agr, body, created_by=uuid.UUID(user["sub"]))
    except IntegrityError:
        raise await _duplicate_ref_error(db, agr_number, body.slip_ref)


@router.patch("/{slip_id}", response_model=SlipResponse)
async def update_slip(
    agreement_id: uuid.UUID, slip_id: uuid.UUID, body: SlipUpdate,
    db: SessionDep, user: SlipWriteDep,
):
    agr = await _get_agreement_or_404(db, agreement_id)
    agr_number = agr.number   # capture before any flush that might fail — see _duplicate_ref_error
    slip = await _get_slip_or_404(db, agreement_id, slip_id)
    try:
        return await slip_crud.update(db, slip, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except IntegrityError:
        raise await _duplicate_ref_error(db, agr_number, body.slip_ref)


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
