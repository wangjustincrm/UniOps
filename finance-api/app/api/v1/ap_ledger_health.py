"""Payable subledger health — which suppliers' open balances can be trusted.

Reads sit under the same finance read authorisation as the rest of the
reporting surface. The two POSTs are the exception to this module being a
report: `/dismiss` and `/restore` record finance's judgement that a stale NC
payable is not real debt, which is the only thing on this page NC has nowhere
to store. They never touch the mirror — see 0035_ap_bill_dismiss.
"""
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.read_authz import authorize_finance_read
from app.db.base import get_db
from app.models.nc_ap import DISMISS_REASONS
from app.services import ap_ledger_health as svc

router = APIRouter(prefix="/ap-ledger-health", tags=["ap-ledger-health"])


async def _authorize_judgement(db: AsyncSession, user: dict) -> tuple[uuid.UUID, str | None]:
    """Who may set a payable aside, and under whose name it is recorded.

    The same finance role set that may read this data — the judgement is a
    finance judgement and belongs to the people who work the list, not to
    whoever happens to be logged in. It is gated on purpose even though the
    read endpoints beside it are not yet: this one writes, and an unattributed
    write that removes payable from a forecast is not recoverable by reading
    the table afterwards.
    """
    await authorize_finance_read(db, user)
    try:
        uid = uuid.UUID(str(user.get("sub", "")))
    except ValueError:
        raise HTTPException(status_code=401, detail="Token has no usable subject")
    return uid, user.get("full_name") or user.get("email")


@router.get("/summary")
async def summary(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await svc.summary(db)


@router.get("/abandoned")
async def abandoned(user: CurrentUser,
                    currency: str | None = Query(None),
                    limit: int = Query(500, ge=1, le=2000),
                    db: AsyncSession = Depends(get_db)):
    """Payable documents NC never approved that still carry a balance — the
    bulk of what looked like an open balance before status was accounted for."""
    return await svc.abandoned_items(db, currency=currency, limit=limit)


@router.get("/gl-clearing")
async def gl_clearing(user: CurrentUser,
                      supplier_name: str = Query(...),
                      currency: str = Query(...),
                      gap: str | None = Query(None),
                      limit: int = Query(200, ge=1, le=1000),
                      db: AsyncSession = Depends(get_db)):
    """Vouchers that moved this supplier's payable without an AP document —
    where a difference settled by a manual journal entry shows up."""
    return await svc.gl_clearing_candidates(db, supplier_name, currency,
                                            gap=gap, limit=limit)


@router.get("/supplier")
async def supplier(user: CurrentUser,
                   supplier_code: str = Query(...),
                   currency: str = Query(...),
                   limit: int = Query(500, ge=1, le=2000),
                   db: AsyncSession = Depends(get_db)):
    """The open bills and the payments behind one supplier's gap — the evidence
    finance needs to judge whether the balance is real."""
    return await svc.supplier_detail(db, supplier_code, currency, limit=limit)


@router.get("/items")
async def items(user: CurrentUser,
                health: str | None = Query(None),
                currency: str | None = Query(None),
                limit: int = Query(200, ge=1, le=1000),
                db: AsyncSession = Depends(get_db)):
    if health is not None and health not in (svc.CONSISTENT, svc.INCONSISTENT):
        raise HTTPException(status_code=422,
                            detail=f"health must be {svc.CONSISTENT} or {svc.INCONSISTENT}")
    return await svc.items(db, health=health, currency=currency, limit=limit)


@router.get("/dismiss-reasons")
async def dismiss_reasons(user: CurrentUser):
    """The closed set of reasons, served rather than duplicated in the UI —
    a hand-copied list on the client is how an option starts 422-ing."""
    return {"reasons": [{"code": k, "label": v} for k, v in DISMISS_REASONS.items()]}


@router.get("/dismissals")
async def dismissals(user: CurrentUser,
                     currency: str | None = Query(None),
                     include_restored: bool = Query(False),
                     limit: int = Query(500, ge=1, le=2000),
                     db: AsyncSession = Depends(get_db)):
    """Everything finance has set aside — the audit trail, and the only way
    back to a bill once it has left the working list."""
    return await svc.dismissal_log(db, currency=currency,
                                   include_restored=include_restored, limit=limit)


@router.post("/dismiss")
async def dismiss(user: CurrentUser,
                  payload: dict = Body(...),
                  db: AsyncSession = Depends(get_db)):
    """Take stale payables off the working list, with a reason on the record."""
    uid, name = await _authorize_judgement(db, user)
    bill_nos = [str(b).strip() for b in (payload.get("bill_nos") or []) if str(b).strip()]
    if not bill_nos:
        raise HTTPException(status_code=422, detail="bill_nos must not be empty")
    if len(bill_nos) > 500:
        raise HTTPException(status_code=422, detail="at most 500 bills at a time")
    reason = str(payload.get("reason") or "").strip()
    if reason not in DISMISS_REASONS:
        raise HTTPException(
            status_code=422,
            detail=f"reason must be one of {sorted(DISMISS_REASONS)}")
    note = (payload.get("note") or "").strip() or None
    # "Other" with no explanation is a dismissal nobody can review later, which
    # defeats the point of recording it at all.
    if reason == "other" and not note:
        raise HTTPException(status_code=422, detail="a note is required when the reason is 'other'")
    return await svc.dismiss_bills(db, bill_nos, reason, note, uid, name)


@router.post("/restore")
async def restore(user: CurrentUser,
                  payload: dict = Body(...),
                  db: AsyncSession = Depends(get_db)):
    """Put bills back on the working list. The dismissal is retired, not
    deleted, so the decision and its reversal both stay on the record."""
    uid, name = await _authorize_judgement(db, user)
    bill_nos = [str(b).strip() for b in (payload.get("bill_nos") or []) if str(b).strip()]
    if not bill_nos:
        raise HTTPException(status_code=422, detail="bill_nos must not be empty")
    return await svc.restore_bills(db, bill_nos, uid, name)
