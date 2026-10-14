"""NC Inventory page: the three NC ledgers as of any date (design §6, §7.2).

All endpoints are gated inventory.view. Amounts (IA amount / unit price, GL
amount) are returned only to callers holding inventory.cost.view; for
everyone else those fields are null and `amounts_visible` is false. This is
enforced here, not in the browser.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.api.v1.perms import ViewDep, can_view_cost
from app.core.deps import SessionDep
from app.models import InvSyncState
from app.services import asof, params

router = APIRouter(prefix="/nc", tags=["nc-inventory"])

_SORT_SAFE = {"material": "mat_code, wh_code, batch", "warehouse": "wh_code, mat_code, batch",
              "batch": "batch, mat_code, wh_code", "qty": "qty desc, mat_code"}


def _s(v) -> str | None:
    """Decimals travel as strings (no float rounding in the browser)."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        v = v.normalize()
        # normalize() can produce '1E+3'; format as plain.
        return format(v, "f")
    return str(v)


async def _asof(db, d: date | None) -> asof.AsOf:
    tz = await params.get_param(db, params.NC_TIME_ZONE_KEY)
    return asof.make_asof(d or asof.today_local(), tz)


class Common:
    def __init__(
        self,
        date_: Annotated[date | None, Query(alias="date")] = None,
        search: Annotated[str | None, Query(max_length=100)] = None,
        class_code: Annotated[str | None, Query(max_length=40)] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=500)] = 50,
    ):
        self.date = date_
        self.search = search.strip() if search and search.strip() else None
        self.class_code = class_code
        self.page = page
        self.page_size = page_size

    def filter_params(self) -> dict:
        return {"search": f"%{self.search}%" if self.search else None,
                "class_code": self.class_code,
                "limit": self.page_size, "offset": (self.page - 1) * self.page_size}


CommonDep = Annotated[Common, Depends()]

_MATERIAL_FILTER = """
    (cast(:class_code as text) is null or m.class_code = :class_code or m.class_code like :class_code || '%')
"""


# ── Meta: what "as of D" means right now ─────────────────────────────────────

async def _period_status(db, a: asof.AsOf) -> dict:
    last_closed = (await db.execute(text("select max(caccountperiod) from inv_nc_ia_period"))).scalar()
    if last_closed and a.period <= last_closed:
        return {"last_closed_period": last_closed, "status": "closed", "uncosted_rows": 0}
    uncosted = (await db.execute(text("""
        select count(*) from inv_nc_ia_ledger
        where caccountperiod = :p and fdispatchflag = 1 and nmny is null"""), {"p": a.period})).scalar_one()
    return {"last_closed_period": last_closed,
            "status": "open_uncosted" if uncosted else "open_costed",
            "uncosted_rows": uncosted}


@router.get("/meta")
async def meta(db: SessionDep, payload: ViewDep,
               date_: Annotated[date | None, Query(alias="date")] = None) -> dict:
    a = await _asof(db, date_)
    state = await db.get(InvSyncState, "nc")
    undated = (await db.execute(text("""
        select count(*) from inv_nc_ic_flow
        where biz_time is null and coalesce(ninnum,0) - coalesce(noutnum,0) <> 0"""))).scalar_one()
    return {
        "date": a.d.isoformat(),
        "today": asof.today_local().isoformat(),
        "is_today": a.d == asof.today_local(),
        "nc_time_zone": a.nc_time_zone,
        "nc_cutoff": a.nc_cutoff.isoformat(sep=" "),
        "period": a.period,
        "month_end": a.month_end,
        "ia": await _period_status(db, a),
        "undated_ic_rows_with_qty": undated,
        "amounts_visible": await can_view_cost(payload, db),
        "sync": {
            "last_success_at": state.last_success_at.isoformat() if state and state.last_success_at else None,
            "status": state.status if state else None,
            "tieout_ok": state.tieout_ok if state else None,
            "tieout_issue_count": state.tieout_issue_count if state else 0,
        },
    }


# ── IC stock ────────────────────────────────────────────────────────────────

_WH_CLASS = "coalesce(c.warehouse_class, 'unclassified')"


def _ic_rows_sql(today: bool) -> str:
    """One row per warehouse × material × batch (× state, today only)."""
    if today:
        # Current on-hand is the only source of stock state and lock. Rows
        # with zero on-hand but a lingering lock (16 such in NC, 2026-10-14)
        # are residue and are counted in the response, not listed.
        return """
            select o.cwarehouseid wh, o.cmaterialoid mat, coalesce(o.vbatchcode,'') batch,
                   max(o.pk_batchcode) pk_batch, o.cstateid state,
                   sum(coalesce(o.nonhandnum,0)) qty, sum(coalesce(o.nlocknum,0)) locked,
                   sum(coalesce(o.nrsnum,0)) reserved
            from inv_nc_ic_onhand o
            where coalesce(o.nonhandnum,0) <> 0
            group by 1,2,3,5"""
    return f"""
        select f.cwarehouseid wh, f.cmaterialoid mat, coalesce(f.vbatchcode,'') batch,
               max(f.pk_batchcode) pk_batch, null::varchar state,
               sum(coalesce(f.ninnum,0) - coalesce(f.noutnum,0)) qty,
               null::numeric locked, null::numeric reserved
        from inv_nc_ic_flow f
        where {asof.IC_FLOW_ASOF}
        group by 1,2,3
        having sum(coalesce(f.ninnum,0) - coalesce(f.noutnum,0)) <> 0"""


@router.get("/stock")
async def stock(
    db: SessionDep, _: ViewDep, q: CommonDep,
    group: Literal["batch", "material"] = "batch",
    warehouse_class: Annotated[list[str] | None, Query()] = None,
    sort: Literal["material", "warehouse", "batch", "qty"] = "material",
) -> dict:
    a = await _asof(db, q.date)
    is_today = a.d == asof.today_local()
    if a.d > asof.today_local():
        raise HTTPException(status_code=422, detail="Date is in the future")
    p = {**a.sql_params, **q.filter_params(), "classes": warehouse_class}

    base = f"""
        with r as ({_ic_rows_sql(is_today)}),
        j as (
            select r.*, w.code wh_code, w.name wh_name, w.iscalculatedinvcost costed,
                   {_WH_CLASS} wh_class,
                   m.code mat_code, m.name mat_name, m.uom_code uom, m.class_code,
                   s.vcode state_code, s.vname state_name,
                   b.vvendbatchcode supplier_batch, b.dproducedate produced, b.dvalidate expires
            from r
            left join inv_nc_warehouse w on w.pk_stordoc = r.wh
            left join inv_warehouse_class c on c.nc_code = w.code
            left join inv_nc_material m on m.pk_material = r.mat
            left join inv_nc_store_state s on s.pk_storestate = r.state
            left join inv_nc_batch b on b.pk_batchcode = r.pk_batch
            where {_MATERIAL_FILTER}
              and (cast(:search as text) is null or m.code ilike :search or m.name ilike :search
                   or m.name_local ilike :search or r.batch ilike :search)
              and (cast(:classes as text[]) is null or {_WH_CLASS} = any(:classes))
        )"""

    summary = (await db.execute(text(base + """
        select wh_class, count(distinct mat) materials, count(*) lines, sum(qty) qty
        from j group by 1 order by 1"""), p)).all()

    if group == "material":
        total = (await db.execute(text(base + "select count(distinct mat) from j"), p)).scalar_one()
        rows = (await db.execute(text(base + f"""
            select mat, mat_code, max(mat_name) mat_name, max(uom) uom, max(class_code) class_code,
                   sum(qty) qty,
                   sum(qty) filter (where wh_class = 'wms') qty_wms,
                   sum(qty) filter (where wh_class = 'external') qty_external,
                   sum(qty) filter (where wh_class = 'production') qty_production,
                   sum(qty) filter (where wh_class not in ('wms','external','production')) qty_other,
                   sum(locked) locked, count(distinct batch) batches
            from j group by mat, mat_code
            order by {'qty desc, mat_code' if sort == 'qty' else 'mat_code'}
            limit :limit offset :offset"""), p)).all()
        items = [{
            "material_code": r.mat_code, "material_name": r.mat_name, "uom": r.uom,
            "class_code": r.class_code, "qty": _s(r.qty), "qty_wms": _s(r.qty_wms),
            "qty_external": _s(r.qty_external), "qty_production": _s(r.qty_production),
            "qty_other": _s(r.qty_other), "locked": _s(r.locked) if is_today else None,
            "batches": r.batches,
        } for r in rows]
    else:
        total = (await db.execute(text(base + "select count(*) from j"), p)).scalar_one()
        rows = (await db.execute(text(base + f"""
            select * from j order by {_SORT_SAFE[sort]} limit :limit offset :offset"""), p)).all()
        items = []
        for r in rows:
            available = None
            if is_today:
                available = max(Decimal(0), (r.qty or 0) - (r.locked or 0) - (r.reserved or 0))
            items.append({
                "warehouse_code": r.wh_code, "warehouse_name": r.wh_name,
                "warehouse_class": r.wh_class, "costed": r.costed,
                "material_code": r.mat_code, "material_name": r.mat_name, "uom": r.uom,
                "class_code": r.class_code, "batch": r.batch or None,
                "supplier_batch": r.supplier_batch,
                "produced": r.produced.isoformat() if r.produced else None,
                "expires": r.expires.isoformat() if r.expires else None,
                "qty": _s(r.qty),
                "locked": _s(r.locked) if is_today else None,
                "available": _s(available),
                "state": r.state_name or r.state_code if is_today else None,
            })

    residue = 0
    if is_today:
        residue = (await db.execute(text("""
            select count(*) from inv_nc_ic_onhand
            where coalesce(nonhandnum,0) = 0 and coalesce(nlocknum,0) <> 0"""))).scalar_one()
    return {
        "date": a.d.isoformat(), "is_today": is_today, "group": group,
        "state_available": is_today,
        "summary": [{"warehouse_class": s.wh_class, "materials": s.materials, "lines": s.lines,
                     "qty": _s(s.qty)} for s in summary],
        "lock_residue_rows": residue,
        "total": total, "page": q.page, "page_size": q.page_size, "items": items,
    }


# ── IA cost ─────────────────────────────────────────────────────────────────

@router.get("/cost")
async def cost(db: SessionDep, payload: ViewDep, q: CommonDep,
               sort: Literal["material", "qty", "amount"] = "material") -> dict:
    a = await _asof(db, q.date)
    show_amt = await can_view_cost(payload, db)
    if sort == "amount" and not show_amt:
        sort = "material"
    p = {**a.sql_params, **q.filter_params()}
    base = f"""
        with r as (
            select l.cinventoryid mat,
                   sum(case when l.fdispatchflag = 0 then 1 else -1 end * coalesce(l.nnum,0)) qty,
                   sum(case when l.fdispatchflag = 0 then 1 else -1 end * coalesce(l.nmny,0)) amt,
                   count(*) filter (where l.caccountperiod = :period and l.fdispatchflag = 1
                                    and l.nmny is null) uncosted
            from inv_nc_ia_ledger l
            where {asof.IA_LEDGER_ASOF}
            group by 1),
        j as (
            select r.*, m.code mat_code, m.name mat_name, m.uom_code uom, m.class_code
            from r left join inv_nc_material m on m.pk_material = r.mat
            where (r.qty <> 0 or r.amt <> 0) and {_MATERIAL_FILTER}
              and (cast(:search as text) is null or m.code ilike :search or m.name ilike :search
                   or m.name_local ilike :search)
        )"""
    totals = (await db.execute(text(base + "select count(*) n, sum(qty) qty, sum(amt) amt from j"), p)).one()
    order = {"material": "mat_code", "qty": "qty desc, mat_code", "amount": "amt desc, mat_code"}[sort]
    rows = (await db.execute(text(base + f"select * from j order by {order} limit :limit offset :offset"), p)).all()
    return {
        "date": a.d.isoformat(), "period": a.period, "ia": await _period_status(db, a),
        "amounts_visible": show_amt,
        "totals": {"materials": totals.n, "qty": _s(totals.qty),
                   "amount": _s(totals.amt) if show_amt else None},
        "total": totals.n, "page": q.page, "page_size": q.page_size,
        "items": [{
            "material_code": r.mat_code, "material_name": r.mat_name, "uom": r.uom,
            "class_code": r.class_code, "qty": _s(r.qty),
            "amount": _s(r.amt) if show_amt else None,
            "unit_price": (_s((r.amt / r.qty).quantize(Decimal("0.000001"))) if show_amt and r.qty else None),
            "uncosted_rows": r.uncosted,
        } for r in rows],
    }


async def _material_pk(db, code: str) -> str:
    pk = (await db.execute(text("select pk_material from inv_nc_material where code = :c"),
                           {"c": code})).scalar()
    if pk is None:
        raise HTTPException(status_code=404, detail=f"Material {code} not found in NC")
    return pk


@router.get("/cost/{material_code}/breakdown")
async def cost_breakdown(material_code: str, db: SessionDep, payload: ViewDep,
                         date_: Annotated[date | None, Query(alias="date")] = None) -> dict:
    """Quantity split by warehouse and batch. IA costs at material level only,
    so this view never carries amounts — the page says so."""
    a = await _asof(db, date_)
    mat = await _material_pk(db, material_code)
    rows = (await db.execute(text(f"""
        select w.code wh_code, coalesce(l.vbatchcode,'') batch,
               sum(case when l.fdispatchflag = 0 then 1 else -1 end * coalesce(l.nnum,0)) qty
        from inv_nc_ia_ledger l left join inv_nc_warehouse w on w.pk_stordoc = l.cstordocid
        where l.cinventoryid = :mat and {asof.IA_LEDGER_ASOF}
        group by 1,2 having sum(case when l.fdispatchflag = 0 then 1 else -1 end * coalesce(l.nnum,0)) <> 0
        order by 1,2"""), {**a.sql_params, "mat": mat})).all()
    return {"material_code": material_code, "date": a.d.isoformat(),
            "items": [{"warehouse_code": r.wh_code, "batch": r.batch or None, "qty": _s(r.qty)} for r in rows]}


@router.get("/cost/{material_code}/lines")
async def cost_lines(material_code: str, db: SessionDep, payload: ViewDep,
                     date_: Annotated[date | None, Query(alias="date")] = None,
                     page: Annotated[int, Query(ge=1)] = 1,
                     page_size: Annotated[int, Query(ge=1, le=500)] = 100) -> dict:
    a = await _asof(db, date_)
    mat = await _material_pk(db, material_code)
    show_amt = await can_view_cost(payload, db)
    p = {**a.sql_params, "mat": mat, "limit": page_size, "offset": (page - 1) * page_size}
    where = f"l.cinventoryid = :mat and {asof.IA_LEDGER_ASOF}"
    total = (await db.execute(text(f"select count(*) from inv_nc_ia_ledger l where {where}"), p)).scalar_one()
    rows = (await db.execute(text(f"""
        select l.*, w.code wh_code, t.code trantype_code, coalesce(t.name_alt, t.name) trantype_name
        from inv_nc_ia_ledger l
        left join inv_nc_warehouse w on w.pk_stordoc = l.cstordocid
        left join inv_nc_trantype t on t.pk_billtypeid = l.ctrantypeid
        where {where}
        order by l.caccountperiod desc, l.biz_time desc nulls last, l.cdetailledgerid
        limit :limit offset :offset"""), p)).all()
    return {
        "material_code": material_code, "date": a.d.isoformat(), "amounts_visible": show_amt,
        "total": total, "page": page, "page_size": page_size,
        "items": [{
            "period": r.caccountperiod, "biz_date": r.dbizdate, "direction": "in" if r.fdispatchflag == 0 else "out",
            "qty": _s(r.nnum), "amount": _s(r.nmny) if show_amt else None,
            "uncosted": r.fdispatchflag == 1 and r.nmny is None,
            "bill_type": r.cbilltypecode, "trantype_code": r.trantype_code, "trantype_name": r.trantype_name,
            "bill_code": r.vbillcode, "source_code": r.vsrccode, "batch": r.vbatchcode,
            "warehouse_code": r.wh_code, "estimate": r.bestimateflag,
        } for r in rows],
    }


# ── GL ledger ───────────────────────────────────────────────────────────────

_GL_POSTED = "(cast(:include_unposted as boolean) or g.tallydate is not null)"


@router.get("/ledger")
async def ledger(db: SessionDep, payload: ViewDep, q: CommonDep,
                 include_unposted: bool = True) -> dict:
    a = await _asof(db, q.date)
    show_amt = await can_view_cost(payload, db)
    p = {**a.sql_params, **q.filter_params(), "include_unposted": include_unposted}
    base = f"""
        with r as (
            select g.account_code acct, max(g.account_name) acct_name, g.material_pk mat,
                   sum(coalesce(g.debit_qty,0) - coalesce(g.credit_qty,0)) qty,
                   sum(coalesce(g.debit_amount,0) - coalesce(g.credit_amount,0)) amt,
                   count(*) filter (where g.tallydate is null) unposted_lines
            from inv_nc_gl_line g
            where {asof.GL_LINE_ASOF} and {_GL_POSTED}
            group by 1,3),
        j as (
            select r.*, m.code mat_code, m.name mat_name, m.uom_code uom, m.class_code
            from r left join inv_nc_material m on m.pk_material = r.mat
            where (r.qty <> 0 or r.amt <> 0)
              and r.acct not like '1471%'
              and (cast(:class_code as text) is null or m.class_code like :class_code || '%')
              and (cast(:search as text) is null or m.code ilike :search or m.name ilike :search
                   or r.acct like :search)
        )"""
    by_account = (await db.execute(text(f"""
        with r as (
            select g.account_code acct, max(g.account_name) acct_name,
                   sum(coalesce(g.debit_qty,0) - coalesce(g.credit_qty,0)) qty,
                   sum(coalesce(g.debit_amount,0) - coalesce(g.credit_amount,0)) amt
            from inv_nc_gl_line g where {asof.GL_LINE_ASOF} and {_GL_POSTED} group by 1)
        select * from r where qty <> 0 or amt <> 0 order by acct"""), p)).all()
    total = (await db.execute(text(base + "select count(*) from j"), p)).scalar_one()
    rows = (await db.execute(text(base + """
        select * from j order by acct, mat_code nulls first limit :limit offset :offset"""), p)).all()

    def acct_row(r):
        return {"account_code": r.acct, "account_name": r.acct_name, "qty": _s(r.qty),
                "amount": _s(r.amt) if show_amt else None}

    return {
        "date": a.d.isoformat(), "period": a.period, "include_unposted": include_unposted,
        "amounts_visible": show_amt,
        "accounts": [acct_row(r) for r in by_account if not r.acct.startswith("1471")],
        # 1471 跌价准备: its own line, never compared per material (design D7).
        "impairment": [acct_row(r) for r in by_account if r.acct.startswith("1471")],
        "total": total, "page": q.page, "page_size": q.page_size,
        "items": [{
            "account_code": r.acct, "account_name": r.acct_name,
            "material_code": r.mat_code, "material_name": r.mat_name if r.mat else "(no material)",
            "uom": r.uom, "qty": _s(r.qty), "amount": _s(r.amt) if show_amt else None,
            "unposted_lines": r.unposted_lines,
        } for r in rows],
    }


@router.get("/ledger/lines")
async def ledger_lines(db: SessionDep, payload: ViewDep,
                       account_code: Annotated[str, Query(max_length=40)],
                       material_code: Annotated[str | None, Query(max_length=60)] = None,
                       date_: Annotated[date | None, Query(alias="date")] = None,
                       include_unposted: bool = True,
                       page: Annotated[int, Query(ge=1)] = 1,
                       page_size: Annotated[int, Query(ge=1, le=500)] = 100) -> dict:
    """Voucher lines behind one ledger row. No material_code = the lines that
    carry no material auxiliary."""
    a = await _asof(db, date_)
    show_amt = await can_view_cost(payload, db)
    mat = await _material_pk(db, material_code) if material_code else None
    p = {**a.sql_params, "acct": account_code, "mat": mat, "include_unposted": include_unposted,
         "limit": page_size, "offset": (page - 1) * page_size}
    where = (f"g.account_code = :acct and {asof.GL_LINE_ASOF} and {_GL_POSTED} and "
             "((cast(:mat as text) is null and g.material_pk is null) or g.material_pk = :mat)")
    total = (await db.execute(text(f"select count(*) from inv_nc_gl_line g where {where}"), p)).scalar_one()
    rows = (await db.execute(text(f"""
        select g.* from inv_nc_gl_line g where {where}
        order by g.year desc, g.period desc, g.voucher_num desc nulls last, g.pk_detail
        limit :limit offset :offset"""), p)).all()
    return {
        "account_code": account_code, "material_code": material_code, "date": a.d.isoformat(),
        "amounts_visible": show_amt, "total": total, "page": page, "page_size": page_size,
        "items": [{
            "year": r.year, "period": r.period, "voucher_num": r.voucher_num,
            "prepared": r.prepareddate, "posted": r.tallydate is not None, "source": r.pk_system,
            "explanation": r.explanation,
            "qty": _s((r.debit_qty or 0) - (r.credit_qty or 0)),
            "amount": _s((r.debit_amount or 0) - (r.credit_amount or 0)) if show_amt else None,
        } for r in rows],
    }
