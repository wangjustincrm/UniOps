"""Trace page: one material (optionally one batch group), NC vs WMS movements
side by side with running balances, pairing and the first divergence.

Gated inventory.view. Quantities only — nothing here carries an amount.
"""
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.api.v1.nc_inventory import _s
from app.api.v1.perms import ViewDep
from app.core.deps import SessionDep
from app.services import asof, trace
from app.services.recon import group_batches, norm_batch

router = APIRouter(prefix="/trace", tags=["trace"])


def _line(ln: trace.Line, balance: Decimal) -> dict:
    return {
        "side": ln.side, "id": ln.id,
        "time": (ln.time.date().isoformat() if ln.date_only else ln.time.isoformat(sep=" ")) if ln.time else None,
        "date_only": ln.date_only, "category": ln.category,
        "category_label": trace.CATEGORY_LABELS.get(ln.category, ln.category),
        "hidden_by_default": ln.category in trace.HIDDEN_BY_DEFAULT,
        "qty": _s(ln.qty), "balance": _s(balance), "doc": ln.doc,
        "pair": ln.pair, "tier": ln.tier, **ln.detail,
    }


@router.get("/materials")
async def material_options(
    db: SessionDep, _: ViewDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """Picker options: materials with any movement to trace — NC stock flows
    in a WMS-managed warehouse, or WMS movements — matched on code or name.
    Materials with something on hand today come first."""
    rows = (await db.execute(text("""
        with nc as (
            select m.code, sum(coalesce(f.ninnum,0) - coalesce(f.noutnum,0)) qty
            from inv_nc_ic_flow f
            join inv_nc_material m on m.pk_material = f.cmaterialoid
            join inv_nc_warehouse w on w.pk_stordoc = f.cwarehouseid
            join inv_warehouse_class c on c.nc_code = w.code and c.warehouse_class = 'wms'
            group by 1),
        wms as (select sku code, sum(qty) qty from inv_wms_move group by 1),
        codes as (select code from nc union select code from wms)
        select k.code, m.name, m.name_local, m.uom_code, nc.qty nc_qty, wms.qty wms_qty,
               nb.since_date no_batch_since
        from codes k
        left join inv_nc_material m on m.code = k.code
        left join nc on nc.code = k.code
        left join wms on wms.code = k.code
        left join inv_no_batch_material nb on nb.material_code = k.code
        where cast(:q as text) is null or k.code ilike :q or m.name ilike :q or m.name_local ilike :q
        order by (coalesce(nc.qty,0) <> 0 or coalesce(wms.qty,0) <> 0) desc, k.code
        limit :limit"""), {"q": f"%{q.strip()}%" if q and q.strip() else None, "limit": limit})).all()
    return {"items": [{
        "code": r.code, "name": r.name or r.name_local, "uom": r.uom_code,
        "in_nc": r.name is not None or r.name_local is not None,
        "nc_qty": _s(r.nc_qty) if r.nc_qty is not None else None,
        "wms_qty": _s(r.wms_qty) if r.wms_qty is not None else None,
        "no_batch_since": r.no_batch_since.isoformat() if r.no_batch_since else None,
    } for r in rows]}


@router.get("/batches")
async def batch_options(
    db: SessionDep, _: ViewDep,
    material: Annotated[str, Query(min_length=1, max_length=60)],
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> dict:
    """Picker options for one material: its batch GROUPS (NC batch and WMS lot
    numbers linked by either batch number — design D10/D16), with today's
    quantity on each side and the last movement date. Groups with stock come
    first, then the most recently moved."""
    code = material.strip().upper()
    pk = (await db.execute(text("select pk_material from inv_nc_material where code = :c"),
                           {"c": code})).scalar()
    wms_lots = [{"lotnum": r.lotnum, "qc_batch": r.qc_batch, "supplier_batch": r.supplier_batch,
                 "qty": r.q or Decimal(0)}
                for r in (await db.execute(text("""
                    select l.lotnum, l.qc_batch, l.supplier_batch, sum(m.qty) q, max(m.txn_time) last
                    from inv_wms_lot l left join inv_wms_move m on m.sku = l.sku and m.lotnum = l.lotnum
                    where l.sku = :c group by 1,2,3"""), {"c": code})).all()]
    last_wms = {r.lotnum: r.last for r in (await db.execute(text(
        "select lotnum, max(txn_time) last from inv_wms_move where sku = :c group by 1"), {"c": code})).all()}
    nc_rows = []
    last_nc: dict[str, datetime] = {}
    if pk:
        for r in (await db.execute(text("""
            select coalesce(f.vbatchcode, '') batch, max(b.vvendbatchcode) supplier_batch,
                   sum(coalesce(f.ninnum,0) - coalesce(f.noutnum,0))
                       filter (where c.warehouse_class = 'wms') q, max(f.biz_time) last
            from inv_nc_ic_flow f
            left join inv_nc_batch b on b.pk_batchcode = f.pk_batchcode
            left join inv_nc_warehouse w on w.pk_stordoc = f.cwarehouseid
            left join inv_warehouse_class c on c.nc_code = w.code
            where f.cmaterialoid = :m and f.vbatchcode is not null
            group by 1"""), {"m": pk})).all():
            nc_rows.append({"batch": r.batch, "supplier_batch": r.supplier_batch,
                            "ic_wms": r.q or Decimal(0), "ic_other": Decimal(0), "ia": Decimal(0)})
            last_nc[r.batch] = r.last
    groups = group_batches(wms_lots, nc_rows)
    needle = "".join((q or "").split()).upper()
    out = []
    for g in groups:
        if g["label"] == "(no batch)":
            continue
        keys = {norm_batch(x) for x in g["nc_batches"] + g["supplier_batches"] + g["wms_lots"]} - {None}
        if needle and not any(needle in k for k in keys):
            continue
        lasts = [last_nc.get(b) for b in g["nc_batches"]] + [last_wms.get(lot) for lot in g["wms_lots"]]
        lasts = [x for x in lasts if x is not None]
        out.append({
            "batch": g["nc_batches"][0] if g["nc_batches"] else (g["supplier_batches"] or [g["label"]])[0],
            "label": g["label"], "nc_batches": g["nc_batches"], "supplier_batches": g["supplier_batches"],
            "wms_lots": len(g["wms_lots"]), "match": g["match"],
            "nc_qty": _s(g["ic_wms"]), "wms_qty": _s(g["wms"]),
            "last_movement": max(lasts).date().isoformat() if lasts else None,
        })
    # With stock today first, then most recently moved.
    out.sort(key=lambda x: (x["nc_qty"] != "0" or x["wms_qty"] != "0", x["last_movement"] or ""), reverse=True)
    return {"material": code, "items": out[:500], "total": len(out)}


@router.get("")
async def trace_material(
    db: SessionDep, _: ViewDep,
    material: Annotated[str, Query(min_length=1, max_length=60)],
    batch: Annotated[str | None, Query(max_length=200)] = None,
    date_: Annotated[date | None, Query(alias="date")] = None,
) -> dict:
    d = date_ or asof.today_local()
    if d > asof.today_local():
        raise HTTPException(status_code=422, detail="Date is in the future")
    code = material.strip().upper()
    t = await trace.load(db, code, d, batch.strip() if batch and batch.strip() else None)
    if not t["material"]["in_nc"] and not t["wms"]:
        raise HTTPException(status_code=404, detail=f"Material {code} is in neither NC nor WMS")
    if t["batch"] and t["no_batch_since"] and t["no_batch_since"] <= d:
        raise HTTPException(status_code=422,
                            detail=f"NC stopped recording {code} by batch on {t['no_batch_since']}; trace the material instead")

    # Running balance per side, in time order.
    def balances(lines, opening):
        out, bal = {}, opening
        for ln in sorted(lines, key=lambda x: (x.time or datetime.min, x.id)):
            bal += ln.qty
            out[ln.id] = bal
        return out, bal

    nc_bal, nc_end = balances(t["nc"], t["nc_opening"])
    wms_bal, wms_end = balances(t["wms"], Decimal(0))

    # Rows: one per pair group, one per unmatched line; ordered by time.
    rows = []
    for g in t["groups"]:
        lines = g["nc"] + g["wms"]
        when = min((ln.time for ln in lines if ln.time), default=None)
        nq = sum((ln.qty for ln in g["nc"]), Decimal(0))
        wq = sum((ln.qty for ln in g["wms"]), Decimal(0))
        rows.append({
            "kind": "pair", "tier": g["tier"], "key": g["key"], "time": when,
            "category": (g["nc"] or g["wms"])[0].category,
            "nc": [_line(ln, nc_bal[ln.id]) for ln in g["nc"]],
            "wms": [_line(ln, wms_bal[ln.id]) for ln in g["wms"]],
            "nc_qty": _s(nq), "wms_qty": _s(wq), "diff": _s(nq - wq),
        })
    for ln in t["nc"] + t["wms"]:
        if ln.pair is None:
            bal = nc_bal if ln.side == "nc" else wms_bal
            rows.append({
                "kind": f"{ln.side}_only", "tier": None, "key": None, "time": ln.time,
                "category": ln.category,
                "nc": [_line(ln, bal[ln.id])] if ln.side == "nc" else [],
                "wms": [_line(ln, bal[ln.id])] if ln.side == "wms" else [],
                "nc_qty": _s(ln.qty) if ln.side == "nc" else "0",
                "wms_qty": _s(ln.qty) if ln.side == "wms" else "0",
                "diff": _s(ln.qty if ln.side == "nc" else -ln.qty),
            })
    rows.sort(key=lambda r: (r["time"] or datetime.min, r["kind"]))

    div = t["divergence"]
    div_out = None
    if div:
        ln = div["line"]
        div_out = {
            "side": ln.side, "id": ln.id, "pair": ln.pair,
            "time": ln.time.isoformat(sep=" ") if ln.time else None,
            "category": ln.category, "diverged_from_start": div["diverged_from_start"],
            "summary": trace.describe(ln, t["groups"]),
            # The largest unmatched DOCUMENT since the balances last agreed.
            "main": ({"side": m["side"], "doc": m["doc"], "qty": _s(m["qty"]), "lines": m["lines"],
                      "time": m["time"].isoformat(sep=" ") if m["time"] else None,
                      "category": m["category"], "summary": trace.describe_doc(m)}
                     if (m := div["main"]) is not None else None),
        }

    # Category summary (the "opening / purchased / issued / returned …" view).
    summ: dict[str, dict] = defaultdict(lambda: {"nc": Decimal(0), "wms": Decimal(0)})
    for ln in t["nc"]:
        summ[ln.category]["nc"] += ln.qty
    for ln in t["wms"]:
        summ[ln.category]["wms"] += ln.qty
    order = list(trace.CATEGORY_LABELS)
    summary = [{"category": c, "label": trace.CATEGORY_LABELS.get(c, c),
                "hidden_by_default": c in trace.HIDDEN_BY_DEFAULT,
                "nc": _s(v["nc"]), "wms": _s(v["wms"]), "diff": _s(v["nc"] - v["wms"])}
               for c, v in sorted(summ.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)]

    pairs = [g for g in t["groups"]]
    return {
        "material": t["material"], "batch": t["batch"], "batch_group": t["batch_group"],
        "batch_scope": t["batch_scope"],
        "no_batch_since": t["no_batch_since"].isoformat() if t["no_batch_since"] else None,
        "date": d.isoformat(), "start": t["start"].isoformat(), "nc_time_zone": t["nc_time_zone"],
        "scope_note": "NC movements in WMS-managed warehouses only; NC times shown in Toronto time.",
        "opening": {"nc": _s(t["nc_opening"]), "wms": "0"},
        "closing": {"nc": _s(nc_end), "wms": _s(wms_end), "diff": _s(nc_end - wms_end)},
        "counts": {
            "nc_lines": len(t["nc"]), "wms_lines": len(t["wms"]),
            "paired_by_document": sum(1 for g in pairs if g["tier"] == 1),
            "paired_by_quantity": sum(1 for g in pairs if g["tier"] == 2),
            "nc_only": sum(1 for ln in t["nc"] if ln.pair is None and ln.category not in trace.HIDDEN_BY_DEFAULT),
            "wms_only": sum(1 for ln in t["wms"] if ln.pair is None and ln.category not in trace.HIDDEN_BY_DEFAULT),
        },
        "divergence": div_out,
        "summary": summary,
        "rows": [{**r, "time": (r["time"].isoformat(sep=" ") if r["time"] else None)} for r in rows],
    }
