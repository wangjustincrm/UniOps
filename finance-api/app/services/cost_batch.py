"""AI Financial Analysis P4 — batch cost (spec §6.6; NC has no such figure).

NC costs a cost object per month: every batch made in a month carries the same
unit cost, and an accounting adjustment that lands in that month is spread
over all of them. This module costs each finished batch from what its own
production order consumed, and follows each self-made input to the batch it
came from, by batch number, across months.

For a batch of material M made by production order(s) X:
  * direct materials = X's consumption (I6), split over X's batches by
    quantity. A self-made input is valued at its OWN batch's actual cost
    (found by material + batch number, recursively); a purchased one at NC's
    issue value.
  * conversion cost = M's conversion allocated in the month × this batch's
    share of M's output that month.
  * accounting adjustments — settlement variances and month-end tails NC
    posted onto X's consumption (IA) — are NOT production cost. They are
    listed separately, with the inherited ones (the difference between what NC
    charged for a self-made input and that input batch's actual cost).
A batch with no production order (20% of 2026 completion receipts, and every
pasteurised-milk batch — milk components are consumed per month without an
order) is costed on its month: that month's consumption plus conversion,
without adjustments or carried-in cost.

Two figures per batch: what NC booked for it (its completion receipt), and its
actual cost. The difference is explained as adjustments on this batch's order,
value inherited from inputs, and NC's monthly averaging.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import cost_analysis as ca
from app.services import cost_diagnosis as dx

MAX_DEPTH = 6


def _f(v):
    return None if v is None else float(v)


async def _receipts(db, material: str, batch: str | None = None, mo: str | None = None):
    sql = ("select period, bill_code, batch_code, mo_code, coalesce(sum(qty),0), "
           "coalesce(sum(amount),0) from nc_ia_ledger where dr = 0 and bill_type = 'I3' "
           "and material_code = :m")
    p = {"m": material}
    if batch is not None:
        sql += " and batch_code = :b"
        p["b"] = batch
    if mo is not None:
        sql += " and mo_code = :o"
        p["o"] = mo
    sql += " group by period, bill_code, batch_code, mo_code order by period"
    return (await db.execute(text(sql), p)).all()


async def _month_basis(db, material: str, period: str) -> dict | None:
    """Actual unit cost of a month's production: consumption + conversion,
    without settlement/tail adjustments or cost carried in."""
    pcs = await ca._product_cost(db, material, period, period)
    if not pcs or not pcs[0]["qty"]:
        return None
    pc = pcs[0]
    actual = pc["consumed_total"] + pc["conversion_total"]
    return {"unit": actual / pc["qty"], "nc_unit": pc["unit"],
            "conversion_unit": pc["conversion_total"] / pc["qty"],
            "adjustments": pc["settlement_total"] + pc["tail_total"],
            "carried": pc["carried_total"]}


async def _conversion_unit(db, material: str, period: str) -> float:
    pcs = await ca._product_cost(db, material, period, period)
    if not pcs or not pcs[0]["qty"]:
        return 0.0
    return pcs[0]["conversion_total"] / pcs[0]["qty"]


async def batch_cost(db: AsyncSession, material: str, batch: str, *, depth: int = 0,
                     cache: dict | None = None) -> dict | None:
    cache = {} if cache is None else cache
    key = (material, batch)
    if key in cache:
        return cache[key]
    rec = await _receipts(db, material, batch=batch)
    if not rec:
        return None
    qty = sum(float(r[4]) for r in rec)
    nc_amt = sum(float(r[5]) for r in rec)
    period = rec[0][0]
    mos = sorted({r[3] for r in rec if r[3]})
    bills = sorted({r[1] for r in rec if r[1]})
    if not mos and bills:
        # One completion receipt can carry several batches of one order with the
        # order number on only one row (S0104 February: 26D043… has it,
        # 26E044… on the same receipt does not). The receipt is the order's.
        mos = sorted({r[0] for r in (await db.execute(text(
            "select distinct mo_code from nc_ia_ledger where dr = 0 and bill_type = 'I3' "
            "and bill_code = any(:b) and mo_code is not null"), {"b": bills})).all()})
    name = await dx._material_name(db, material)
    out = {"material_code": material, "material_name": name, "batch": batch, "period": period,
           "mo_codes": mos, "qty": qty, "nc": {"amount": nc_amt, "unit": nc_amt / qty if qty else None},
           "inputs": [], "adjustments": [], "children": [], "notes": []}
    cache[key] = out    # guard against cycles before recursing

    if not mos:
        out["basis"] = "month"
        by_month: dict = defaultdict(float)
        for r in rec:
            by_month[r[0]] += float(r[4])
        actual, conv, used = 0.0, 0.0, []
        for per, q_ in sorted(by_month.items()):
            mb = await _month_basis(db, material, per)
            if mb is None:
                nc_part = sum(float(r[5]) for r in rec if r[0] == per)
                actual += nc_part
                out["notes"].append(f"{per}: no product cost for the month — NC's figure used.")
                continue
            actual += mb["unit"] * q_
            conv += mb["conversion_unit"] * q_
            used.append(f"{per} ({dx.u(mb['unit'])}/unit"
                        + (f", leaving out {dx.m(mb['adjustments'])} of adjustments"
                           if abs(mb["adjustments"]) >= 0.01 else "")
                        + (f" and {dx.m(mb['carried'])} carried in" if abs(mb["carried"]) >= 0.01 else "")
                        + ")")
        out["actual"] = {"amount": actual, "unit": actual / qty if qty else None}
        out["conversion"] = conv
        if used:
            out["notes"].insert(0, "No production order: costed on its month's production as a "
                                   "whole — consumption and conversion only — " + "; ".join(used))
        out["variance"] = out["nc"]["amount"] - out["actual"]["amount"]
        out["explained"] = {"month_adjustments": out["variance"]}
        return out

    out["basis"] = "batch"
    # this batch's share of its orders' output
    # Everything the orders completed: rows carrying the order number, and the
    # rows on the same completion receipts that do not.
    mo_out = float((await db.execute(text(
        "select coalesce(sum(qty),0) from nc_ia_ledger where dr = 0 and bill_type = 'I3' "
        "and material_code = :m and (mo_code = any(:o) or bill_code in (select bill_code "
        "from nc_ia_ledger where dr = 0 and bill_type = 'I3' and mo_code = any(:o)))"),
        {"m": material, "o": mos})).scalar() or 0)
    share = qty / mo_out if mo_out else 1.0
    out["share_of_orders"] = share

    rows = (await db.execute(text(
        "select bill_type, material_code, max(material_name), batch_code, bill_code, src_code, "
        "coalesce(sum(qty),0), coalesce(sum(amount),0) from nc_ia_ledger "
        "where dr = 0 and mo_code = any(:o) and bill_type in ('I6', 'IA') "
        "group by bill_type, material_code, batch_code, bill_code, src_code"),
        {"o": mos})).all()
    groups: dict = defaultdict(lambda: {"qty": 0.0, "nc": 0.0, "name": None})
    adjustments = []
    for bt, mat, nm, b, bill, src, q, a in rows:
        if bt == "IA":
            adjustments.append({"kind": "adjustment on this order", "material_code": mat,
                                "amount": float(a) * share, "ref": bill, "attached_to": src})
            continue
        g = groups[(mat, b)]
        g["qty"] += float(q) * share
        g["nc"] += float(a) * share
        g["name"] = nm
    actual_total = 0.0
    inherited = 0.0
    for (mat, b), g in sorted(groups.items(), key=lambda kv: -abs(kv[1]["nc"])):
        item = {"material_code": mat, "material_name": g["name"], "batch": b, "qty": g["qty"],
                "nc_amount": g["nc"], "actual_amount": g["nc"], "source": "purchased"}
        child = None
        if b and depth < MAX_DEPTH and await _receipts(db, mat, batch=b):
            child = await batch_cost(db, mat, b, depth=depth + 1, cache=cache)
        if child and child.get("actual") and child["actual"]["unit"] is not None:
            item["actual_amount"] = child["actual"]["unit"] * g["qty"]
            item["source"] = f"batch {b}"
            item["actual_unit"] = child["actual"]["unit"]
            out["children"].append(child)
        else:
            made = await _made_month(db, mat, out["period"])
            if made:
                mb = await _month_basis(db, mat, made)
                if mb:
                    item["actual_amount"] = mb["unit"] * g["qty"]
                    item["actual_unit"] = mb["unit"]
                    item["source"] = f"{made} production (batch not traceable)"
                    out["notes"].append(f"{mat} batch {b or '—'} could not be traced; valued at "
                                        f"{made}'s actual production cost.")
        inherited += item["nc_amount"] - item["actual_amount"]
        actual_total += item["actual_amount"]
        out["inputs"].append(item)
    conv = await _conversion_unit(db, material, period) * qty
    out["conversion"] = conv
    actual_total += conv
    out["adjustments"] = adjustments
    adj_total = sum(a["amount"] for a in adjustments)
    out["actual"] = {"amount": actual_total, "unit": actual_total / qty if qty else None}
    out["variance"] = out["nc"]["amount"] - actual_total
    nc_built = sum(i["nc_amount"] for i in out["inputs"]) + conv + adj_total
    out["explained"] = {
        "adjustments_on_this_order": adj_total,
        "inherited_from_inputs": inherited,
        "nc_monthly_averaging": out["nc"]["amount"] - nc_built,
    }
    return out


async def _month_qty(db, material, period) -> float | None:
    v = (await db.execute(text(
        "select coalesce(sum(qty),0) from nc_cm_prod_costs where dr = 0 and material_code = :m "
        "and period = :p"), {"m": material, "p": period})).scalar()
    return float(v) if v else None


async def _made_month(db, material, period):
    return (await db.execute(text(
        "select max(period) from nc_cm_prod_costs where dr = 0 and material_code = :m "
        "and period <= :p and qty > 0"), {"m": material, "p": period})).scalar()


def _root_variance(node: dict) -> list[dict]:
    """Where the inherited value comes from: walk inputs whose NC value
    differs from their actual cost, down to the deepest such batch."""
    path = []
    cur = node
    while True:
        worst = None
        for ch in cur.get("children", []):
            v = ch.get("variance") or 0
            if worst is None or abs(v) > abs(worst.get("variance") or 0):
                worst = ch
        if not worst or abs(worst.get("variance") or 0) < 0.01 * max(abs(cur["nc"]["amount"]), 1):
            break
        path.append(worst)
        cur = worst
    return path


def summarise(b: dict) -> dict:
    """The conclusion for one batch, with its evidence."""
    nc_u, ac_u = b["nc"]["unit"], b["actual"]["unit"]
    diff = b["variance"]
    lead = (f"{b['material_code']} batch {b['batch']} ({b['period']}, {dx.q(b['qty'])}): NC booked "
            f"{dx.m(b['nc']['amount'])} ({dx.u(nc_u)}/unit); its actual production cost is "
            f"{dx.m(b['actual']['amount'])} ({dx.u(ac_u)}/unit).")
    because, evidence = [], []
    if abs(diff) < max(0.005 * abs(b["actual"]["amount"] or 0), 1):
        because.append("The two agree: no accounting adjustment or averaging moved this batch.")
    else:
        ex = b.get("explained") or {}
        parts = [(k, v) for k, v in ex.items() if abs(v) >= 0.01]
        labels = {"adjustments_on_this_order": "adjustments NC posted onto this batch's consumption",
                  "inherited_from_inputs": "value inherited from its inputs (what NC charged for "
                                           "them minus their own batches' actual cost)",
                  "nc_monthly_averaging": "NC's monthly averaging across the month's batches",
                  "month_adjustments": "settlement/tail adjustments and cost carried in, in that "
                                       "month's NC cost"}
        if parts:
            because.append(f"The difference of {dx.m(diff)} is " + "; ".join(
                f"{dx.m(v)} {labels[k]}" for k, v in sorted(parts, key=lambda kv: -abs(kv[1]))) + ".")
        root = _root_variance(b)
        if root:
            chain = " ← ".join([b["material_code"]] + [f"{r['material_code']} {r['batch']}" for r in root])
            deep = root[-1]
            because.append(
                f"Followed batch by batch: {chain}. The deepest is {deep['material_code']} batch "
                f"{deep['batch']} ({deep['period']}), booked by NC at {dx.u(deep['nc']['unit'])}/unit "
                f"against an actual {dx.u(deep['actual']['unit'])}/unit"
                + (f" — {deep['notes'][0].rstrip('.')}" if deep.get("notes") else "") + ".")
    if b.get("basis") == "month":
        because += b["notes"]
    for i in b.get("inputs", [])[:8]:
        evidence.append(dx._ev(f"{i['material_code']} {i['batch'] or ''}".strip(),
                               f"{dx.q(i['qty'])} · NC {dx.m(i['nc_amount'])} · actual "
                               f"{dx.m(i['actual_amount'])} ({i['source']})"))
    if b.get("conversion"):
        evidence.append(dx._ev("Conversion cost share", dx.m(b["conversion"])))
    for a in b.get("adjustments", []):
        evidence.append(dx._ev(f"Adjustment {a.get('material_code') or ''}".strip(),
                               dx.m(a["amount"]), a.get("ref")))
    return {"headline": lead, "because": because, "evidence": evidence}


async def batches(db: AsyncSession, material: str, period_from: str, period_to: str) -> dict:
    rec = (await db.execute(text(
        "select batch_code, min(period), coalesce(sum(qty),0), coalesce(sum(amount),0), "
        "string_agg(distinct coalesce(mo_code, ''), ',') from nc_ia_ledger "
        "where dr = 0 and bill_type = 'I3' and material_code = :m and period between :a and :b "
        "and batch_code is not null group by batch_code order by min(period), batch_code"),
        {"m": material, "a": period_from, "b": period_to})).all()
    out, cache = [], {}
    for b, per, q, a, mos in rec:
        bc = await batch_cost(db, material, b, cache=cache)
        if not bc:
            continue
        out.append({"batch": b, "period": per, "qty": float(q),
                    "nc_unit": bc["nc"]["unit"], "actual_unit": bc["actual"]["unit"],
                    "variance": bc["variance"], "basis": bc["basis"],
                    # resolved through the completion receipt when the row has none
                    "mo_codes": bc["mo_codes"]})
    no_batch = (await db.execute(text(
        "select count(*) from nc_ia_ledger where dr = 0 and bill_type = 'I3' and material_code = :m "
        "and period between :a and :b and batch_code is null"),
        {"m": material, "a": period_from, "b": period_to})).scalar()
    worst = max(out, key=lambda x: abs(x["variance"] or 0), default=None)
    if worst and abs(worst["variance"] or 0) >= 1:
        headline = (f"{material}: {len(out)} batch(es); the largest gap between NC's booking and the "
                    f"actual cost is batch {worst['batch']} ({worst['period']}): NC "
                    f"{dx.u(worst['nc_unit'])}/unit vs actual {dx.u(worst['actual_unit'])}/unit.")
    else:
        headline = f"{material}: {len(out)} batch(es); NC's booking matches the actual cost."
    return {"material": material, "headline": headline, "batches": out,
            "receipts_without_batch": no_batch}
