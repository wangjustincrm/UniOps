"""Trace page: one material (optionally one batch group), NC vs WMS movements
side by side with running balances, pairing and the first divergence.

Gated inventory.view. Quantities only — nothing here carries an amount.
"""
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.v1.nc_inventory import _s
from app.api.v1.perms import ViewDep
from app.core.deps import SessionDep
from app.services import asof, trace

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
