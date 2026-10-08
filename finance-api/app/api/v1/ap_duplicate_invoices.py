"""AP duplicate invoices — invoice numbers NC has on more than one payable.

Reads and writes are both gated on the finance role set: the rows are
supplier payables and payment evidence, which is finance data, and the one
write records a finance judgement under the reviewer's name. The findings are
computed from the NC mirror on every request (app/services/ap_duplicate_invoices.py);
only the verdict is stored.
"""
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.ap_ledger_health import _authorize_judgement
from app.core.deps import CurrentUser
from app.core.read_authz import authorize_finance_read
from app.db.base import get_db
from app.models.nc_ap import DUP_REVIEW_REASONS
from app.services import ap_duplicate_invoices as svc

router = APIRouter(prefix="/ap-duplicate-invoices", tags=["ap-duplicate-invoices"])


@router.get("")
async def report(user: CurrentUser,
                 kind: str | None = Query(None),
                 reviewed: str = Query("no", pattern="^(no|yes|all)$"),
                 currency: str | None = Query(None),
                 q: str | None = Query(None, max_length=100),
                 limit: int = Query(500, ge=1, le=2000),
                 db: AsyncSession = Depends(get_db)):
    """Findings, biggest exposure first, plus a summary over ALL of them."""
    await authorize_finance_read(db, user)
    if kind is not None and kind not in svc.KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {list(svc.KINDS)}")
    flag = {"no": False, "yes": True, "all": None}[reviewed]
    return await svc.report(db, kind=kind, reviewed=flag, currency=currency or None,
                            q=(q or "").strip() or None, limit=limit)


@router.get("/reasons")
async def reasons(user: CurrentUser):
    """Served, not duplicated in the UI — see ap_ledger_health's /dismiss-reasons."""
    return {"reasons": [{"code": k, "label": v} for k, v in DUP_REVIEW_REASONS.items()]}


@router.post("/review")
async def review(user: CurrentUser,
                 payload: dict = Body(...),
                 db: AsyncSession = Depends(get_db)):
    """Record that a group has been looked at, and why it needs nothing more.

    `bill_nos` must be the bills the reviewer was shown; if the group has
    changed since, the verdict is refused (409) instead of covering a copy
    nobody saw.
    """
    uid, name = await _authorize_judgement(db, user)
    key = str(payload.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="key is required")
    bill_nos = [str(b).strip() for b in (payload.get("bill_nos") or []) if str(b).strip()]
    if len(bill_nos) < 2:
        raise HTTPException(status_code=422,
                            detail="bill_nos must list the bills you reviewed")
    reason = str(payload.get("reason") or "").strip()
    if reason not in DUP_REVIEW_REASONS:
        raise HTTPException(status_code=422,
                            detail=f"reason must be one of {sorted(DUP_REVIEW_REASONS)}")
    note = (payload.get("note") or "").strip() or None
    if reason == "other" and not note:
        raise HTTPException(status_code=422, detail="a note is required when the reason is 'other'")
    result = await svc.review(db, key, bill_nos, reason, note, uid, name)
    if not result["applied"]:
        status = 404 if result["reason"] == "not_found" else 409
        raise HTTPException(status_code=status, detail=result)
    return result


@router.post("/unreview")
async def unreview(user: CurrentUser,
                   payload: dict = Body(...),
                   db: AsyncSession = Depends(get_db)):
    """Put a group back on the list. The verdict is retired, not deleted."""
    uid, name = await _authorize_judgement(db, user)
    key = str(payload.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="key is required")
    return await svc.unreview(db, key, uid, name)
