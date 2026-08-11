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
from app.crud.agreement_slip import RETIRED as RETIRED_SLIP_STATUSES
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
# Deliberately its OWN key, not epms.agreement.write: recording a pickup slip
# and editing the agreement's own terms (vendor/schedule/status) are separate
# powers — see identity 0007_slip_write_perm for the rationale and grant set.
SlipRecordDep = Annotated[dict, Depends(require_permission("epms.agreement.slip.write"))]


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


# Postgres SQLSTATEs we can tell apart. asyncpg exposes them as `.sqlstate`
# on the wrapped driver exception; psycopg as `.pgcode`.
_PG_UNIQUE_VIOLATION = "23505"


def _sqlstate(exc: IntegrityError) -> str | None:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


async def _slip_integrity_error(
    db: SessionDep, exc: IntegrityError, agreement_id: uuid.UUID,
    agr_number: str, slip_ref: str | None,
) -> HTTPException:
    """Turn an IntegrityError from a slip write into a 409 that says what
    actually went wrong.

    The partial unique index on (agreement_id, slip_ref) is a real collision
    path — the same paper slip re-entered by a second person, or a client
    retry after a timeout — not an edge case. It surfaces as IntegrityError,
    which crud never raises as ValueError, so it must be caught here rather
    than falling through to the generic 500 the get_session dependency would
    otherwise let through.

    Whole-branch review (I3, and review Minor 6): this used to be a catch-all
    that reported EVERY IntegrityError as "slip reference already recorded" —
    a picked_by pointing at a user that doesn't exist got the same message,
    sending the recorder off to fix a slip_ref that was never the problem.
    And even on a genuine duplicate it never said WHICH slip holds the ref or
    what state that slip is in, which is the one fact that tells the recorder
    whether they can act on it. Now: identify the live conflicting row and
    name it; anything else says so honestly instead of borrowing the
    duplicate-ref story.

    A failed flush leaves the session in "pending rollback", and even a PLAIN
    attribute read on an already-loaded ORM object (e.g. agr.number) tries to
    transparently re-fetch it — first raising PendingRollbackError (session
    unusable), and after an explicit rollback(), MissingGreenlet (the
    lazy-load's implicit IO has no async context to run in from a synchronous
    f-string expression). That's why the caller passes the agreement NUMBER as
    a plain str captured before the failing call, instead of the ORM object —
    nothing here touches `agr` at all. The rollback below is required anyway
    so the session is usable again, both for the lookup right after it and for
    get_session's own teardown.
    """
    await db.rollback()

    # sqlstate is None only if some driver doesn't expose it; in that case
    # fall back to the lookup, which is self-verifying either way — it only
    # produces the duplicate message when a conflicting live row really is
    # sitting there.
    if slip_ref is not None and _sqlstate(exc) in (_PG_UNIQUE_VIOLATION, None):
        conflict = (await db.execute(
            select(AgreementPickupSlip).where(
                AgreementPickupSlip.agreement_id == agreement_id,
                AgreementPickupSlip.slip_ref == slip_ref,
                AgreementPickupSlip.status.notin_(RETIRED_SLIP_STATUSES),
            ).order_by(AgreementPickupSlip.created_at)
        )).scalars().first()
        if conflict is not None:
            # A `reconciled` conflict needs different advice: void() rejects it
            # (VOIDABLE is open/pending_ap_review) and ap_review() only accepts
            # pending_ap_review, so telling the clerk to "void or reject that
            # slip" would send them straight into a second 409. The only way
            # out is to detach the invoice holding it, which releases the slip
            # back to `open` via _release_agreement_evidence.
            if conflict.status == "reconciled":
                remedy = (f"It has already been claimed by an invoice. Re-match or "
                          f"detach that invoice first — the slip returns to 'open' "
                          f"and its reference is free again.")
            else:
                remedy = ("Void or reject that slip if it was entered in error, "
                          "then record this one again.")
            return HTTPException(
                status_code=409,
                detail=f"Slip reference '{slip_ref}' is already recorded for "
                       f"agreement {agr_number} by slip {conflict.id}, which is "
                       f"'{conflict.status}'. {remedy}",
            )

    constraint = getattr(getattr(exc, "orig", None), "constraint_name", None)
    return HTTPException(
        status_code=409,
        detail="Could not save this pickup slip: the database rejected it"
               + (f" (constraint {constraint})" if constraint else "")
               + ". Check that the person it is recorded against still exists "
                 "as a user, then try again.",
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
    agreement_id: uuid.UUID, body: SlipCreate, db: SessionDep, user: SlipRecordDep,
):
    agr = await _get_agreement_or_404(db, agreement_id)
    agr_number = agr.number   # capture before any flush that might fail — see _slip_integrity_error
    try:
        return await slip_crud.create(db, agr, body, created_by=uuid.UUID(user["sub"]))
    except IntegrityError as exc:
        raise await _slip_integrity_error(db, exc, agreement_id, agr_number, body.slip_ref)


@router.patch("/{slip_id}", response_model=SlipResponse)
async def update_slip(
    agreement_id: uuid.UUID, slip_id: uuid.UUID, body: SlipUpdate,
    db: SessionDep, user: SlipRecordDep,
):
    agr = await _get_agreement_or_404(db, agreement_id)
    agr_number = agr.number   # capture before any flush that might fail — see _slip_integrity_error
    slip = await _get_slip_or_404(db, agreement_id, slip_id)
    try:
        return await slip_crud.update(db, slip, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except IntegrityError as exc:
        raise await _slip_integrity_error(db, exc, agreement_id, agr_number, body.slip_ref)


@router.delete("/{slip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def void_slip(
    agreement_id: uuid.UUID, slip_id: uuid.UUID, db: SessionDep, user: SlipRecordDep,
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
