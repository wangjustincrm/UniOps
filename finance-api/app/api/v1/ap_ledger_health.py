"""Payable subledger health — which suppliers' open balances can be trusted.

Read-only. Sits under the same finance read authorisation as the rest of the
reporting surface.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.services import ap_ledger_health as svc

router = APIRouter(prefix="/ap-ledger-health", tags=["ap-ledger-health"])


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
