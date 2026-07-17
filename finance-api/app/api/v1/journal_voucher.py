"""Journal voucher API — list/detail + lifecycle actions (Plan 2)."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from uniops_authz import has_permission

from app.core.deps import CurrentUser
from app.crud import journal_voucher as crud
from app.db.base import get_db
from app.models.journal_voucher import JournalVoucher, JournalVoucherLine

router = APIRouter(prefix="/journal-vouchers", tags=["journal-vouchers"])

# sort 参数直接进 order_by,故只接受白名单列 (SQL 注入边界)。source_subsystem 由 Task 2 加。
_SORTABLE = {
    "voucher_date": JournalVoucher.voucher_date,
    "jv_number": JournalVoucher.jv_number,
    "summary": JournalVoucher.summary,
    "total_debit": JournalVoucher.total_debit,
    "status": JournalVoucher.status,
    "source_subsystem": JournalVoucher.source_subsystem,
}

_SUBSYSTEM_LABELS = {
    "GL": "General Ledger", "AP": "Accounts Payable", "AR": "Accounts Receivable",
    "FA": "Fixed Assets", "CM": "Cash Management", "IA": "Inventory Accounting",
    "EGL": "Exchange Gain/Loss", "PLCF": "Gain/Loss Carry-Forward",
    # OT and any future NC code fall back to the raw code — a human names the map,
    # code doesn't guess (spec §2.4).
}


def _subsystem_label(code: str | None) -> str | None:
    if code is None:
        return None
    return _SUBSYSTEM_LABELS.get(code, code)


class IdsIn(BaseModel):
    ids: list[uuid.UUID]


def _hdr(jv: JournalVoucher) -> dict:
    return {
        "id": str(jv.id), "jv_number": jv.jv_number, "voucher_word": jv.voucher_word,
        "voucher_date": jv.voucher_date.isoformat(), "fiscal_period": jv.fiscal_period,
        "summary": jv.summary, "status": jv.status,
        # In the header (not just the detail) so the list can tell NC mirrors
        # apart: their status follows NC's tally and the crud verbs reject any
        # hand-change, so offering them for batch review/post only ever 4xxs.
        "nc_source_pk": jv.nc_source_pk,
        "source_subsystem": jv.source_subsystem,
        "source_subsystem_label": _subsystem_label(jv.source_subsystem),
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
                        source_subsystem: str | None = Query(default=None),
                        q: str | None = Query(default=None),
                        limit: int = Query(default=50, le=200),
                        offset: int = Query(default=0, ge=0),
                        sort: str = Query(default="voucher_date"),
                        dir: str = Query(default="desc")):
    base = select(JournalVoucher)
    if period:
        base = base.where(JournalVoucher.fiscal_period == period)
    if status:
        base = base.where(JournalVoucher.status == status)
    if source_doc_type:
        base = base.where(JournalVoucher.source_doc_type == source_doc_type)
    if source_subsystem:
        base = base.where(JournalVoucher.source_subsystem == source_subsystem)
    if q:
        like = f"%{q}%"
        base = base.where(or_(JournalVoucher.jv_number.ilike(like),
                              JournalVoucher.summary.ilike(like)))
    total = (await db.execute(
        select(func.count()).select_from(base.subquery()))).scalar_one()
    if sort not in _SORTABLE:
        raise HTTPException(status_code=422, detail=f"unknown sort column {sort!r}")
    col = _SORTABLE[sort]
    col = col.asc() if dir == "asc" else col.desc()
    rows = (await db.execute(
        base.order_by(col, JournalVoucher.jv_number.desc())   # jv_number 作稳定次级键
        .offset(offset).limit(limit))).scalars().all()
    return {"total": total, "items": [_hdr(jv) for jv in rows]}


@router.get("/permissions")
async def jv_permissions(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    """UI capability gate — same finance.jv.post permission the lifecycle
    actions enforce (app.crud.journal_voucher._require_role), via the same
    has_permission() primitive so system_admin is reported as `can_act`
    regardless of whether a grant row exists for the key."""
    uid = uuid.UUID(str(user.get("sub", "")))
    can_act = await has_permission(db, uid, user.get("role", ""), "finance.jv.post")
    return {"can_act": can_act}


@router.get("/{jv_id}")
async def get_voucher(jv_id: uuid.UUID, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    jv = await crud.get(db, jv_id)
    if jv is None:
        raise HTTPException(status_code=404, detail="Journal voucher not found")
    lines = (await db.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id == jv_id)
        .order_by(JournalVoucherLine.line_no))).scalars().all()

    from app.models.coa import ChartOfAccount
    from app.models.journal_voucher import JvLineDimension
    from app.models.mirrors import CostCenter, Department, User

    async def _lookup(model, ids, key=lambda r: r.id):
        ids = {i for i in ids if i is not None}
        if not ids:
            return {}
        rows = (await db.execute(select(model).where(model.id.in_(ids)))).scalars().all()
        return {key(r): r for r in rows}

    users = await _lookup(User, {jv.prepared_by, jv.reviewed_by, jv.posted_by})
    ccs = await _lookup(CostCenter, {ln.cost_center_id for ln in lines})
    depts = await _lookup(Department, {ln.department_id for ln in lines})
    codes = {ln.account_code for ln in lines if ln.account_code}
    coa = {}
    if codes:
        coa = {a.code: a for a in (await db.execute(
            select(ChartOfAccount).where(ChartOfAccount.code.in_(codes)))).scalars()}
    dims_by_line: dict = {}
    line_ids = [ln.id for ln in lines]
    if line_ids:
        for d in (await db.execute(select(JvLineDimension).where(
                JvLineDimension.jv_line_id.in_(line_ids)))).scalars():
            dims_by_line.setdefault(d.jv_line_id, []).append(
                {"dim_code": d.dim_code, "value_text": d.value_text})

    def _name(uid):
        u = users.get(uid)
        return u.full_name if u else None

    def _iso(dt):
        return dt.isoformat() if dt else None

    voucher = _hdr(jv) | {
        "source_service": jv.source_service,
        "prepared_by_name": _name(jv.prepared_by), "prepared_at": _iso(jv.prepared_at),
        "reviewed_by_name": _name(jv.reviewed_by), "reviewed_at": _iso(jv.reviewed_at),
        "posted_by_name": _name(jv.posted_by), "posted_at": _iso(jv.posted_at),
    }

    def _line(ln: JournalVoucherLine) -> dict:
        cc, dept = ccs.get(ln.cost_center_id), depts.get(ln.department_id)
        acct = coa.get(ln.account_code) if ln.account_code else None
        return {
            "line_no": ln.line_no, "account_code": ln.account_code,
            "account_name": acct.name if acct else None, "summary": ln.summary,
            "orig_debit": str(ln.orig_debit), "orig_credit": str(ln.orig_credit),
            "local_debit": str(ln.local_debit), "local_credit": str(ln.local_credit),
            "currency": ln.currency, "fx_rate": str(ln.fx_rate),
            "quantity": str(ln.quantity) if ln.quantity is not None else None,
            "unit": ln.unit,
            "price": str(ln.price) if ln.price is not None else None,
            "cost_center_code": cc.code if cc else None,
            "cost_center_name": cc.name if cc else None,
            "department_code": dept.code if dept else None,
            "department_name": dept.name if dept else None,
            "partner_name": ln.partner_name, "tax_code": ln.tax_code,
            "dims": dims_by_line.get(ln.id, []),
        }

    return {"voucher": voucher, "lines": [_line(ln) for ln in lines]}


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
