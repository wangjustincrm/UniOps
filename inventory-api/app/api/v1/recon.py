"""Reconciliation page: WMS × NC stock × NC cost × NC GL on any date (design §7.3).

Gated inventory.view; every amount (IA/GL amounts, GL−IA amount, amount
parts of categories, GL-by-source breakdown, impairment) is withheld without
inventory.cost.view.
"""
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.v1.nc_inventory import _s
from app.api.v1.perms import ViewDep, can_view_cost
from app.core.deps import SessionDep
from app.services import asof, params, recon

router = APIRouter(prefix="/recon", tags=["reconciliation"])


@router.get("")
async def reconciliation(
    db: SessionDep, payload: ViewDep,
    date_: Annotated[date | None, Query(alias="date")] = None,
    expand_batches: bool = False,
    only_differences: bool = False,
    status: Annotated[list[Literal["ok", "explained", "unexplained"]] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
    class_code: Annotated[str | None, Query(max_length=40)] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    include_unposted: bool = True,
    sort: Literal["material", "ic_wms", "ia_ic", "gl_ia"] = "material",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    tz = await params.get_param(db, params.NC_TIME_ZONE_KEY)
    d = date_ or asof.today_local()
    if d > asof.today_local():
        raise HTTPException(status_code=422, detail="Date is in the future")
    a = asof.make_asof(d, tz)
    qty_tol = Decimal(str(await params.get_param(db, params.QTY_TOLERANCE_KEY)))
    amt_tol = Decimal(str(await params.get_param(db, params.AMOUNT_TOLERANCE_KEY)))
    show_amt = await can_view_cost(payload, db)

    rows = await recon.load(db, a, include_unposted)
    for r in rows.values():
        recon.attribute(r, d, qty_tol, amt_tol)
    statuses = {code: recon.status_of(r, qty_tol, amt_tol) for code, r in rows.items()}

    # Totals over everything (before filtering), so the cards never change
    # with the table filters.
    def tot(attr):
        return sum((getattr(r, attr) for r in rows.values() if getattr(r, attr) is not None), Decimal(0))

    totals = {
        "wms": _s(tot("wms")) if d >= recon.WMS_GO_LIVE else None,
        "ic_wms": _s(tot("ic_wms")), "ic_external": _s(tot("ic_external")),
        "ic_production": _s(tot("ic_production")), "ic_transient": _s(tot("ic_transient")),
        "ic_total": _s(sum((r.ic_total for r in rows.values()), Decimal(0))),
        "ia_qty": _s(tot("ia_qty")), "gl_qty": _s(tot("gl_qty")),
        "ia_amount": _s(tot("ia_amt")) if show_amt else None,
        "gl_amount": _s(tot("gl_amt")) if show_amt else None,
        "materials": len(rows),
        "by_status": {s: sum(1 for v in statuses.values() if v == s) for s in ("ok", "explained", "unexplained")},
    }

    # Filters.
    sel = list(rows.values())
    if only_differences:
        sel = [r for r in sel if statuses[r.code] != "ok"]
    if status:
        sel = [r for r in sel if statuses[r.code] in status]
    if category:
        sel = [r for r in sel if any(c["code"] in category for c in r.categories)]
    if class_code:
        sel = [r for r in sel if (r.class_code or "").startswith(class_code)]
    if search:
        s = search.strip().lower()
        sel = [r for r in sel if s in r.code.lower() or s in (r.name or "").lower()]

    def key(r):
        res = r.residual
        if sort == "ic_wms":
            return (-abs(res["ic_wms"] or 0), r.code)
        if sort == "ia_ic":
            return (-abs(res["ia_ic"]), r.code)
        if sort == "gl_ia":
            return (-(abs(res["gl_ia_amount"]) if show_amt else abs(res["gl_ia_qty"])), r.code)
        return (r.code,)

    sel.sort(key=key)
    total = len(sel)
    page_rows = sel[(page - 1) * page_size: page * page_size]

    batches = {}
    if expand_batches:
        expandable = [r.code for r in page_rows if not (r.no_batch_since and r.no_batch_since <= d)]
        batches = await recon.load_batches(db, a, expandable)

    def cat_out(c):
        return {"code": c["code"], "label": recon.CATEGORIES[c["code"]][1], "side": c["side"],
                "qty": _s(c["qty"]), "amount": _s(c["amount"]) if show_amt and c["amount"] else None}

    items = []
    for r in page_rows:
        res = r.residual
        no_batch = bool(r.no_batch_since and r.no_batch_since <= d)
        item = {
            "material_code": r.code, "material_name": r.name, "uom": r.uom, "class_code": r.class_code,
            "in_nc": r.in_nc, "wms_uom": r.wms_uom, "unit_check": recon.unit_check(r.uom, r.wms_uom), "no_batch_since": r.no_batch_since.isoformat() if r.no_batch_since else None,
            "wms": _s(r.wms), "ic_wms": _s(r.ic_wms), "ic_external": _s(r.ic_external),
            "ic_production": _s(r.ic_production), "ic_transient": _s(r.ic_transient),
            "ic_total": _s(r.ic_total), "ia_qty": _s(r.ia_qty), "gl_qty": _s(r.gl_qty),
            "ia_amount": _s(r.ia_amt) if show_amt else None,
            "gl_amount": _s(r.gl_amt) if show_amt else None,
            "diff": {
                "ic_wms": _s(r.ic_wms - r.wms) if r.wms is not None else None,
                "ia_ic": _s(r.ia_qty - r.ic_total),
                "gl_ia_qty": _s(r.gl_qty - r.ia_qty),
                "gl_ia_amount": _s(r.gl_amt - r.ia_amt) if show_amt else None,
            },
            "unexplained": {
                "ic_wms": _s(res["ic_wms"]), "ia_ic": _s(res["ia_ic"]),
                "gl_ia_qty": _s(res["gl_ia_qty"]),
                "gl_ia_amount": _s(res["gl_ia_amount"]) if show_amt else None,
            },
            "categories": [cat_out(c) for c in r.categories],
            "gl_by_source": ({k: _s(v) for k, v in r.gl_by_source.items()} if show_amt else None),
            "status": statuses[r.code],
            "batch_expandable": not no_batch,
        }
        if expand_batches and not no_batch:
            groups = batches.get(r.code, [])
            item["batches"] = [{
                **{k: g[k] for k in ("label", "nc_batches", "wms_lots", "supplier_batches", "match")},
                "wms": _s(g["wms"]) if r.wms is not None else None,
                "ic_wms": _s(g["ic_wms"]), "ic_other": _s(g["ic_other"]), "ia": _s(g["ia"]),
                "diff_ic_wms": _s(g["ic_wms"] - g["wms"]) if r.wms is not None else None,
            } for g in groups]
            # C10: totals agree but batches do not → it is a labelling difference.
            if (r.wms is not None and res["ic_wms"] is not None and abs(res["ic_wms"]) <= qty_tol
                    and any(abs(g["ic_wms"] - g["wms"]) > qty_tol for g in groups)):
                item["categories"].append({"code": "batch_label", "label": recon.CATEGORIES["batch_label"][1],
                                           "side": "ic_wms", "qty": "0", "amount": None})
                if item["status"] == "ok":
                    item["status"] = "explained"
        items.append(item)

    extra = await recon.unattributable(db, a, include_unposted)

    def gl_extra(r):
        return {"account_code": r.account_code, "account_name": r.name, "qty": _s(r.q),
                "amount": _s(r.amt) if show_amt else None}

    return {
        "date": d.isoformat(), "nc_time_zone": tz, "period": a.period,
        "wms_live": d >= recon.WMS_GO_LIVE, "include_unposted": include_unposted,
        "amounts_visible": show_amt, "tolerance": {"qty": str(qty_tol), "amount": str(amt_tol)},
        "categories": [{"code": k, "side": v[0], "label": v[1]} for k, v in recon.CATEGORIES.items()],
        "totals": totals,
        "total": total, "page": page, "page_size": page_size, "items": items,
        "gl_impairment": [gl_extra(r) for r in extra["impairment"]],
        "gl_no_material": [gl_extra(r) for r in extra["no_material"]],
    }
