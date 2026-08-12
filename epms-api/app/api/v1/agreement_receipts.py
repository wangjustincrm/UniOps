"""House-account agreement receipt endpoints (entry / list / void / AP review)."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.v1.invoices import ApDep
from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.crud import agreement as agr_crud
from app.crud import agreement_receipt as receipt_crud
from app.crud.agreement_receipt import RETIRED as RETIRED_RECEIPT_STATUSES
from app.models.agreement import PurchaseAgreement
from app.models.agreement_receipt import AgreementReceipt
from app.schemas.agreement_receipt import (
    ReceiptApReview,
    ReceiptCreate,
    ReceiptListResponse,
    ReceiptResponse,
    ReceiptUpdate,
)

router = APIRouter(prefix="/agreements/{agreement_id}/receipts", tags=["agreement-receipts"])

ReceiptReadDep = Annotated[dict, Depends(require_permission("epms.agreement.read"))]
# Deliberately its OWN key, not epms.agreement.write: recording an agreement
# receipt and editing the agreement's own terms (vendor/schedule/status) are
# separate powers — see identity 0007_receipt_write_perm for the rationale
# and grant set.
ReceiptRecordDep = Annotated[dict, Depends(require_permission("epms.agreement.receipt.write"))]


async def _get_agreement_or_404(db: SessionDep, agreement_id: uuid.UUID) -> PurchaseAgreement:
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return agr


async def _get_receipt_or_404(
    db: SessionDep, agreement_id: uuid.UUID, receipt_id: uuid.UUID,
) -> AgreementReceipt:
    receipt = (await db.execute(
        select(AgreementReceipt).where(
            AgreementReceipt.id == receipt_id,
            AgreementReceipt.agreement_id == agreement_id,
        )
    )).scalar_one_or_none()
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return receipt


# Postgres SQLSTATEs we can tell apart. asyncpg exposes them as `.sqlstate`
# on the wrapped driver exception; psycopg as `.pgcode`.
_PG_UNIQUE_VIOLATION = "23505"


def _sqlstate(exc: IntegrityError) -> str | None:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


async def _receipt_integrity_error(
    db: SessionDep, exc: IntegrityError, agreement_id: uuid.UUID,
    agr_number: str, receipt_ref: str | None,
) -> HTTPException:
    """Turn an IntegrityError from a receipt write into a 409 that says what
    actually went wrong.

    The partial unique index on (agreement_id, receipt_ref) is a real
    collision path — the same paper receipt re-entered by a second person, or
    a client retry after a timeout — not an edge case. It surfaces as
    IntegrityError, which crud never raises as ValueError, so it must be
    caught here rather than falling through to the generic 500 the
    get_session dependency would otherwise let through.

    Whole-branch review (I3, and review Minor 6): this used to be a catch-all
    that reported EVERY IntegrityError as "receipt reference already
    recorded" — a received_by pointing at a user that doesn't exist got the
    same message, sending the recorder off to fix a receipt_ref that was
    never the problem. And even on a genuine duplicate it never said WHICH
    receipt holds the ref or what state that receipt is in, which is the one
    fact that tells the recorder whether they can act on it. Now: identify
    the live conflicting row and name it; anything else says so honestly
    instead of borrowing the duplicate-ref story.

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
    if receipt_ref is not None and _sqlstate(exc) in (_PG_UNIQUE_VIOLATION, None):
        conflict = (await db.execute(
            select(AgreementReceipt).where(
                AgreementReceipt.agreement_id == agreement_id,
                AgreementReceipt.receipt_ref == receipt_ref,
                AgreementReceipt.status.notin_(RETIRED_RECEIPT_STATUSES),
            ).order_by(AgreementReceipt.created_at)
        )).scalars().first()
        if conflict is not None:
            # A `reconciled` conflict needs different advice: void() rejects it
            # (VOIDABLE is open/pending_ap_review) and ap_review() only accepts
            # pending_ap_review, so telling the clerk to "void or reject that
            # receipt" would send them straight into a second 409. The only way
            # out is to detach the invoice holding it, which releases the
            # receipt back to `open` via _release_agreement_evidence.
            if conflict.status == "reconciled":
                remedy = (f"It has already been claimed by an invoice. Re-match or "
                          f"detach that invoice first — the receipt returns to 'open' "
                          f"and its reference is free again.")
            else:
                remedy = ("Void or reject that receipt if it was entered in error, "
                          "then record this one again.")
            return HTTPException(
                status_code=409,
                detail=f"Receipt reference '{receipt_ref}' is already recorded for "
                       f"agreement {agr_number} by receipt {conflict.id}, which is "
                       f"'{conflict.status}'. {remedy}",
            )

    constraint = getattr(getattr(exc, "orig", None), "constraint_name", None)
    return HTTPException(
        status_code=409,
        detail="Could not save this receipt: the database rejected it"
               + (f" (constraint {constraint})" if constraint else "")
               + ". Check that the person it is recorded against still exists "
                 "as a user, then try again.",
    )


@router.get("", response_model=ReceiptListResponse)
async def list_receipts(
    agreement_id: uuid.UUID,
    db: SessionDep,
    user: ReceiptReadDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
):
    await _get_agreement_or_404(db, agreement_id)
    items = await receipt_crud.list_for_agreement(db, agreement_id, status=status_filter)
    return {"items": items, "total": len(items)}


@router.post("", response_model=ReceiptResponse, status_code=status.HTTP_201_CREATED)
async def create_receipt(
    agreement_id: uuid.UUID, body: ReceiptCreate, db: SessionDep, user: ReceiptRecordDep,
):
    agr = await _get_agreement_or_404(db, agreement_id)
    agr_number = agr.number   # capture before any flush that might fail — see _receipt_integrity_error
    try:
        return await receipt_crud.create(db, agr, body, created_by=uuid.UUID(user["sub"]))
    except IntegrityError as exc:
        raise await _receipt_integrity_error(db, exc, agreement_id, agr_number, body.receipt_ref)


@router.patch("/{receipt_id}", response_model=ReceiptResponse)
async def update_receipt(
    agreement_id: uuid.UUID, receipt_id: uuid.UUID, body: ReceiptUpdate,
    db: SessionDep, user: ReceiptRecordDep,
):
    agr = await _get_agreement_or_404(db, agreement_id)
    agr_number = agr.number   # capture before any flush that might fail — see _receipt_integrity_error
    receipt = await _get_receipt_or_404(db, agreement_id, receipt_id)
    try:
        return await receipt_crud.update(db, receipt, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except IntegrityError as exc:
        raise await _receipt_integrity_error(db, exc, agreement_id, agr_number, body.receipt_ref)


@router.delete("/{receipt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def void_receipt(
    agreement_id: uuid.UUID, receipt_id: uuid.UUID, db: SessionDep, user: ReceiptRecordDep,
):
    await _get_agreement_or_404(db, agreement_id)
    receipt = await _get_receipt_or_404(db, agreement_id, receipt_id)
    try:
        await receipt_crud.void(db, receipt)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{receipt_id}/ap-review", response_model=ReceiptResponse)
async def ap_review_receipt(
    agreement_id: uuid.UUID, receipt_id: uuid.UUID, body: ReceiptApReview,
    db: SessionDep, user: ApDep,
):
    await _get_agreement_or_404(db, agreement_id)
    receipt = await _get_receipt_or_404(db, agreement_id, receipt_id)
    try:
        return await receipt_crud.ap_review(db, receipt, body.action, uuid.UUID(user["sub"]))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
