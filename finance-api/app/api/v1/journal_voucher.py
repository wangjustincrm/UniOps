"""Journal voucher API — list/detail + lifecycle actions (Plan 2)."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.crud import journal_voucher as crud
from app.db.base import get_db
from app.models.journal_voucher import JournalVoucher, JournalVoucherLine

router = APIRouter(prefix="/journal-vouchers", tags=["journal-vouchers"])


class IdsIn(BaseModel):
    ids: list[uuid.UUID]


def _hdr(jv: JournalVoucher) -> dict:
    return {
        "id": str(jv.id), "jv_number": jv.jv_number, "voucher_word": jv.voucher_word,
        "voucher_date": jv.voucher_date.isoformat(), "fiscal_period": jv.fiscal_period,
        "summary": jv.summary, "status": jv.status,
        "source_doc_type": jv.source_doc_type,
        "source_doc_id": str(jv.source_doc_id) if jv.source_doc_id else None,
        "source_doc_number": jv.source_doc_number,
        "total_debit": str(jv.total_debit), "total_credit": str(jv.total_credit),
        "total_local_debit": str(jv.total_local_debit),
        "total_local_credit": str(jv.total_local_credit),
        "reverses_jv_id": str(jv.reverses_jv_id) if jv.reverses_jv_id else None,
        "reversed_by_jv_id": str(jv.reversed_by_jv_id) if jv.reversed_by_jv_id else None,
    }


@router.get("")
async def list_vouchers(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        period: str | None = Query(default=None),
                        status: str | None = Query(default=None),
                        source_doc_type: str | None = Query(default=None),
                        q: str | None = Query(default=None),
                        limit: int = Query(default=50, le=200),
                        offset: int = Query(default=0, ge=0)):
    base = select(JournalVoucher)
    if period:
        base = base.where(JournalVoucher.fiscal_period == period)
    if status:
        base = base.where(JournalVoucher.status == status)
    if source_doc_type:
        base = base.where(JournalVoucher.source_doc_type == source_doc_type)
    if q:
        like = f"%{q}%"
        base = base.where(or_(JournalVoucher.jv_number.ilike(like),
                              JournalVoucher.summary.ilike(like)))
    total = (await db.execute(
        select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(
        base.order_by(JournalVoucher.voucher_date.desc(),
                      JournalVoucher.jv_number.desc())
        .offset(offset).limit(limit))).scalars().all()
    return {"total": total, "items": [_hdr(jv) for jv in rows]}


@router.get("/{jv_id}")
async def get_voucher(jv_id: uuid.UUID, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    jv = await crud.get(db, jv_id)
    if jv is None:
        raise HTTPException(status_code=404, detail="Journal voucher not found")
    lines = (await db.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id == jv_id)
        .order_by(JournalVoucherLine.line_no))).scalars().all()
    return {"voucher": _hdr(jv), "lines": [
        {"line_no": ln.line_no, "account_code": ln.account_code, "summary": ln.summary,
         "orig_debit": str(ln.orig_debit), "orig_credit": str(ln.orig_credit),
         "local_debit": str(ln.local_debit), "local_credit": str(ln.local_credit),
         "currency": ln.currency, "fx_rate": str(ln.fx_rate),
         "partner_name": ln.partner_name, "tax_code": ln.tax_code}
        for ln in lines]}


def _err(e: Exception):
    from app.crud.journal_voucher import JvPermissionError, JvStateError
    if isinstance(e, JvPermissionError):
        return HTTPException(status_code=403, detail=str(e))
    if isinstance(e, JvStateError):
        return HTTPException(status_code=409, detail=str(e))
    raise e


async def _act(db, jv_id, user, fn):
    from app.crud.journal_voucher import JvPermissionError, JvStateError
    try:
        jv = await fn(db, jv_id, user)
        await db.commit()
        return _hdr(jv)
    except (JvPermissionError, JvStateError) as e:
        await db.rollback()
        raise _err(e)


@router.post("/{jv_id}/review")
async def review(jv_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await _act(db, jv_id, user, crud.review)


@router.post("/{jv_id}/unreview")
async def unreview(jv_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await _act(db, jv_id, user, crud.unreview)


@router.post("/{jv_id}/post")
async def post(jv_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await _act(db, jv_id, user, crud.post)


@router.post("/{jv_id}/unpost")
async def unpost(jv_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await _act(db, jv_id, user, crud.unpost)


@router.post("/{jv_id}/reverse")
async def reverse(jv_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await _act(db, jv_id, user, crud.reverse)


@router.post("/review-batch")
async def review_batch(body: IdsIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    res = await crud.review_batch(db, body.ids, user)
    await db.commit()
    return res


@router.post("/post-batch")
async def post_batch(body: IdsIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    res = await crud.post_batch(db, body.ids, user)
    await db.commit()
    return res
