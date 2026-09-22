"""Journal voucher API — list/detail + lifecycle actions (Plan 2)."""
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from uniops_authz import has_permission

from app.core.deps import CurrentUser
from app.crud import journal_voucher as crud
from app.db.base import get_db
from app.models.journal_voucher import (JournalVoucher, JournalVoucherLine,
                                        JvLineDimension)

router = APIRouter(prefix="/journal-vouchers", tags=["journal-vouchers"])

# sort 参数直接进 order_by,故只接受白名单列 (SQL 注入边界)。source_subsystem 由 Task 2 加。
_SORTABLE = {
    "voucher_date": JournalVoucher.voucher_date,
    "jv_number": JournalVoucher.jv_number,
    "summary": JournalVoucher.summary,
    # The list's "Debit (CAD)" column displays total_local_debit, so sort on the
    # SAME column — sorting the original-currency total_debit would make the
    # visible CAD column non-monotonic for a multi-currency book (27k USD lines).
    "total_debit": JournalVoucher.total_local_debit,
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


# NC VOUCHERKIND, measured against the live book 2026-09-22 (period spread and
# explanations both checked): 1 appears only in each year's period 12 with
# "Manually year end adjustment"-style summaries, 2 only in period 00 at zero
# amount, 3 carries 转材料成本/结转完工产品入库成本, 4 is "Carry forward of R&D".
# A code we have not characterised keeps its raw number rather than a guess.
_KIND_LABELS = {
    0: "Ordinary", 1: "Year-end adjustment", 2: "Opening",
    3: "Cost carry-forward", 4: "R&D carry-forward",
}

# 正常 / 错误 / 作废 / 暂存. Only `normal` ever reaches status=posted.
VOUCHER_STATES = ("normal", "error", "discarded", "tempsave")


def _subsystem_label(code: str | None) -> str | None:
    if code is None:
        return None
    return _SUBSYSTEM_LABELS.get(code, code)


def _has_line(*conds):
    """A voucher-level predicate that holds when ANY of its lines matches."""
    return select(JournalVoucherLine.id).where(
        JournalVoucherLine.jv_id == JournalVoucher.id, *conds).exists()


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
        # NC header facts. These are what NC's own voucher list shows in its
        # 制单 / 审核 / 记账 columns; before the 0038 sync they were all NULL.
        "nc_num": jv.nc_num,
        "nc_prepared_name": jv.nc_prepared_name,
        "nc_checked_name": jv.nc_checked_name,
        "nc_manager_name": jv.nc_manager_name,
        "nc_voucher_type_name": jv.nc_voucher_type_name,
        "nc_attachment_count": jv.nc_attachment_count,
        "nc_voucher_state": jv.nc_voucher_state,
        "nc_voucher_kind": jv.nc_voucher_kind,
        "nc_voucher_kind_label": _KIND_LABELS.get(jv.nc_voucher_kind),
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
                        period_from: str | None = Query(default=None),
                        period_to: str | None = Query(default=None),
                        date_from: date | None = Query(default=None),
                        date_to: date | None = Query(default=None),
                        num_from: int | None = Query(default=None),
                        num_to: int | None = Query(default=None),
                        status: str | None = Query(default=None),
                        voucher_state: str | None = Query(default=None),
                        voucher_kind: int | None = Query(default=None),
                        prepared_by: str | None = Query(default=None),
                        checked_by: str | None = Query(default=None),
                        manager: str | None = Query(default=None),
                        account_code: str | None = Query(default=None),
                        opposite_subject: str | None = Query(default=None),
                        currency: str | None = Query(default=None),
                        amount_min: Decimal | None = Query(default=None),
                        amount_max: Decimal | None = Query(default=None),
                        aux_code: str | None = Query(default=None),
                        aux_value: str | None = Query(default=None),
                        source_doc_type: str | None = Query(default=None),
                        source_subsystem: str | None = Query(default=None),
                        q: str | None = Query(default=None),
                        limit: int = Query(default=50, le=200),
                        offset: int = Query(default=0, ge=0),
                        sort: str = Query(default="voucher_date"),
                        dir: str = Query(default="desc")):
    """NC's 凭证查询 has period/date/number RANGES and an auxiliary picker; this
    used to offer a single exact fiscal_period, so listing a quarter meant three
    separate queries and listing "all of 2026" was impossible.

    `period` (exact) is kept because existing callers and saved links use it.
    fiscal_period is a zero-padded 'YYYY-MM' string, so a plain string BETWEEN
    orders correctly — no date casting needed."""
    base = select(JournalVoucher)
    if period:
        base = base.where(JournalVoucher.fiscal_period == period)
    if period_from:
        base = base.where(JournalVoucher.fiscal_period >= period_from)
    if period_to:
        base = base.where(JournalVoucher.fiscal_period <= period_to)
    if date_from:
        base = base.where(JournalVoucher.voucher_date >= date_from)
    if date_to:
        base = base.where(JournalVoucher.voucher_date <= date_to)
    if num_from is not None:
        base = base.where(JournalVoucher.nc_num >= num_from)
    if num_to is not None:
        base = base.where(JournalVoucher.nc_num <= num_to)
    if status:
        base = base.where(JournalVoucher.status == status)
    if voucher_state:
        base = base.where(JournalVoucher.nc_voucher_state == voucher_state)
    if voucher_kind is not None:
        base = base.where(JournalVoucher.nc_voucher_kind == voucher_kind)
    if prepared_by:
        base = base.where(JournalVoucher.nc_prepared_name == prepared_by)
    if checked_by:
        base = base.where(JournalVoucher.nc_checked_name == checked_by)
    if manager:
        base = base.where(JournalVoucher.nc_manager_name == manager)
    if source_doc_type:
        base = base.where(JournalVoucher.source_doc_type == source_doc_type)
    if source_subsystem:
        base = base.where(JournalVoucher.source_subsystem == source_subsystem)
    if amount_min is not None:
        base = base.where(JournalVoucher.total_local_debit >= amount_min)
    if amount_max is not None:
        base = base.where(JournalVoucher.total_local_debit <= amount_max)
    # Line-level predicates are EXISTS subqueries, not joins: a voucher with two
    # matching lines must appear once, and a join would also break the count.
    if account_code:
        base = base.where(_has_line(JournalVoucherLine.account_code.startswith(account_code)))
    if opposite_subject:
        base = base.where(_has_line(
            JournalVoucherLine.opposite_subject.ilike(f"%{opposite_subject}%")))
    if currency:
        base = base.where(_has_line(JournalVoucherLine.currency == currency))
    if aux_code:
        dim = select(JvLineDimension.id).where(
            JvLineDimension.jv_line_id == JournalVoucherLine.id,
            JvLineDimension.dim_code == aux_code)
        if aux_value:
            dim = dim.where(JvLineDimension.value_text == aux_value)
        base = base.where(_has_line(dim.exists()))
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


@router.get("/filter-options")
async def filter_options(_: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Values the voucher-query form offers. Read from the data rather than
    hard-coded: the preparer list is whoever NC actually recorded (24 users on
    the live book), and the auxiliary list is whichever dimensions this book
    really uses — NC's catalog carries far more than any one book touches."""
    async def _distinct(col):
        rows = (await db.execute(
            select(col).where(col.is_not(None)).distinct().order_by(col))).scalars().all()
        return [r for r in rows if r]

    aux_codes = (await db.execute(
        select(JvLineDimension.dim_code).distinct()
        .order_by(JvLineDimension.dim_code))).scalars().all()
    return {
        "prepared_by": await _distinct(JournalVoucher.nc_prepared_name),
        "checked_by": await _distinct(JournalVoucher.nc_checked_name),
        "manager": await _distinct(JournalVoucher.nc_manager_name),
        "voucher_states": list(VOUCHER_STATES),
        "voucher_kinds": [{"value": k, "label": v} for k, v in sorted(_KIND_LABELS.items())],
        "aux_codes": list(aux_codes),
    }


@router.get("/aux-values")
async def aux_values(_: CurrentUser, dim_code: str = Query(...),
                     q: str | None = Query(default=None),
                     limit: int = Query(default=50, le=200),
                     db: AsyncSession = Depends(get_db)):
    """Values in use for one auxiliary, for the query form's value picker.
    Typeahead rather than a full list: 物料基本信息 alone has ~2,000 values."""
    stmt = select(JvLineDimension.value_text).where(
        JvLineDimension.dim_code == dim_code, JvLineDimension.value_text.is_not(None))
    if q:
        stmt = stmt.where(JvLineDimension.value_text.ilike(f"%{q}%"))
    rows = (await db.execute(
        stmt.distinct().order_by(JvLineDimension.value_text).limit(limit))).scalars().all()
    return {"items": list(rows)}


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
