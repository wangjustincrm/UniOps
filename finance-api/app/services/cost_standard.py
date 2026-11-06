"""AI Financial Analysis P5 — standard cost model suggestion (spec §6.7).

A SUGGESTION, written nowhere: NC keeps costing actuals. For a product it
proposes a standard cost card, rolled up level by level, and replays 2026
against it to show which variances standard costing would have produced —
and, above all, which of the actual swings would never have reached product
cost (a settlement variance becomes a purchase price variance; a month-end
tail does not happen).

  usage       per production order (material consumption, never picking —
              §6.0): each input's quantity per unit of output, the median over
              2026's orders, orders more than K median-absolute-deviations
              away left out. Products without orders (pasteurised milk) use
              months. Compared with the default NC BOM (bom_lines.qty_per is
              per UNIT of output — verified by mass balance).
  price       purchased inputs: the last WINDOW months of settled / invoiced
              receipts, quantity-weighted — never a provisional price. Self-
              made inputs: their own standard, recursively.
  conversion  conversion cost per unit over the last WINDOW months, stoppage
              elements left out. NC spreads conversion by quantity produced,
              the same per kg for every product (measured: Feb 16.0831, Jul
              19.3955); whether that is the right basis for a standard is
              Finance's decision — the card says so and does not decide it.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import cost_analysis as ca
from app.services import cost_diagnosis as dx

DEFAULTS = {"window_months": 6, "mad_k": 3.0, "bom_tolerance": 0.05, "exclude_stoppage": True}
MAX_DEPTH = 6


def _months_back(period: str, n: int) -> str:
    p = period
    for _ in range(n - 1):
        p = ca.prev_period(p)
    return p


def _robust_median(values: list[float], k: float) -> tuple[float | None, list[float], int]:
    if not values:
        return None, [], 0
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values]) if len(values) > 2 else 0
    kept = [v for v in values if mad == 0 or abs(v - med) <= k * mad] if len(values) > 2 else values
    return (statistics.median(kept) if kept else med), kept, len(values) - len(kept)


async def _bom(db: AsyncSession, material: str) -> dict:
    """Default NC BOM: component → qty per unit of output."""
    try:
        rows = (await db.execute(text(
            "select l.component_material_code, l.qty_per, l.uom from bom_lines l "
            "join boms b on b.id = l.bom_id left join nc_bom n on n.cbomid = b.nc_source_pk "
            "where b.product_material_code = :m and coalesce(n.hbdefault, 'N') = 'Y'"),
            {"m": material})).all()
    except Exception:                       # noqa: BLE001 — BOM tables are MRP's; absent → no BOM
        await db.rollback()
        return {}
    out: dict = {}
    for code, qp, uom in rows:
        out[code] = {"qty_per": float(qp or 0) + out.get(code, {}).get("qty_per", 0.0), "uom": uom}
    return out


async def _order_usage(db: AsyncSession, material: str, period_from: str, period_to: str):
    """Per production order: output, and each input's consumption.

    An order's output is every completion row carrying it, plus the rows on the
    same completion receipts that do not (see cost_batch)."""
    rows = (await db.execute(text(
        "with mos as (select distinct mo_code from nc_ia_ledger where dr = 0 and bill_type = 'I3' "
        "  and material_code = :m and mo_code is not null and period between :a and :b), "
        "out as (select o.mo_code, coalesce(sum(r.qty),0) q from mos o join nc_ia_ledger r "
        "  on r.dr = 0 and r.bill_type = 'I3' and r.material_code = :m and (r.mo_code = o.mo_code "
        "  or r.bill_code in (select bill_code from nc_ia_ledger where dr = 0 and bill_type = 'I3' "
        "  and mo_code = o.mo_code)) group by o.mo_code) "
        "select out.mo_code, out.q, i.material_code, max(i.material_name), coalesce(sum(i.qty),0), "
        "min(i.period) from out join nc_ia_ledger i on i.dr = 0 and i.bill_type = 'I6' "
        "and i.mo_code = out.mo_code group by out.mo_code, out.q, i.material_code"),
        {"m": material, "a": period_from, "b": period_to})).all()
    orders: dict = defaultdict(lambda: {"out": 0.0, "inputs": {}, "period": None})
    names = {}
    for mo, q, inp, nm, qty, per in rows:
        o = orders[mo]
        o["out"] = float(q)
        o["inputs"][inp] = float(qty)
        o["period"] = min(o["period"] or per, per)
        names[inp] = nm
    return orders, names


async def _month_usage(db: AsyncSession, material: str, period_from: str, period_to: str):
    """Products without orders: each month's NC product cost lines."""
    rows = (await db.execute(text(
        "select p.period, p.qty, l.material_code, max(l.material_name), coalesce(sum(l.qty),0) "
        "from nc_cm_prod_costs p join nc_cm_prod_cost_lines l on l.prod_cost_pk = p.nc_pk and l.dr = 0 "
        "where p.dr = 0 and p.material_code = :m and p.period between :a and :b and p.qty > 0 "
        "and l.material_code is not null group by p.period, p.qty, l.material_code"),
        {"m": material, "a": period_from, "b": period_to})).all()
    months: dict = defaultdict(lambda: {"out": 0.0, "inputs": {}})
    names = {}
    for per, q, inp, nm, qty in rows:
        months[per]["out"] = float(q)
        months[per]["inputs"][inp] = float(qty)
        months[per]["period"] = per
        names[inp] = nm
    return months, names


async def _purchase_price(db: AsyncSession, material: str, asof: str, window: int) -> dict | None:
    """Quantity-weighted settled / invoiced receipt price over the window —
    provisional prices never count."""
    start = _months_back(asof, window)
    rows = [r for r in await ca._ledger(db, material=material)
            if start <= r.period <= asof and r.category in ("settlement", "purchase") and r.qty > 0]
    if rows:
        q = sum(float(r.qty) for r in rows)
        a = sum(float(r.amount) for r in rows)
        return {"unit": a / q, "basis": f"{len(rows)} settled/invoiced receipt line(s), {start}–{asof}",
                "refs": sorted({r.src_code or r.bill_code for r in rows})[:5]}
    cons = [r for r in await ca._ledger(db, material=material)
            if start <= r.period <= asof and r.category == "consumption" and r.qty > 0]
    if cons:
        q = sum(float(r.qty) for r in cons)
        a = sum(float(r.amount) for r in cons)
        return {"unit": a / q, "basis": f"no receipt in the window — consumption price {start}–{asof}",
                "refs": []}
    return None


async def _conversion_rate(db: AsyncSession, material: str, asof: str, window: int,
                           exclude_stoppage: bool) -> dict:
    start = _months_back(asof, window)
    rows = (await db.execute(text(
        "select s.period, s.element_name, coalesce(sum(s.amount),0) from nc_cm_stuff_lines s "
        "join nc_cm_prod_costs p on p.cost_object_pk = s.cost_object_pk and p.period = s.period "
        "and p.dr = 0 where s.dr = 0 and s.source_type = 24 and p.material_code = :m "
        "and s.period between :a and :b group by s.period, s.element_name"),
        {"m": material, "a": start, "b": asof})).all()
    out = dict((await db.execute(text(
        "select period, coalesce(sum(qty),0) from nc_cm_prod_costs where dr = 0 and material_code = :m "
        "and period between :a and :b and qty > 0 group by period"),
        {"m": material, "a": start, "b": asof})).all())
    conv, stopped = 0.0, 0.0
    for per, el, amt in rows:
        if per not in out:
            continue
        if exclude_stoppage and el and "stoppage" in el.lower():
            stopped += float(amt)
            continue
        conv += float(amt)
    qty = sum(float(v) for v in out.values())
    return {"unit": conv / qty if qty else 0.0, "months": sorted(out), "excluded_stoppage": stopped}


async def standard_card(db: AsyncSession, material: str, period_from: str, period_to: str,
                        params: dict | None = None, *, depth: int = 0,
                        cache: dict | None = None) -> dict:
    p = {**DEFAULTS, **(params or {})}
    cache = {} if cache is None else cache
    if material in cache:
        return cache[material]
    name = await dx._material_name(db, material)
    card = {"material_code": material, "material_name": name, "lines": [], "notes": []}
    cache[material] = card          # cycle guard
    orders, names = await _order_usage(db, material, period_from, period_to)
    basis = "production orders"
    samples = [o for o in orders.values() if o["out"] > 0]
    if not samples:
        months, names = await _month_usage(db, material, period_from, period_to)
        samples = [m for m in months.values() if m["out"] > 0]
        basis = "months (no production orders)"
    card["usage_basis"] = basis
    card["samples"] = len(samples)
    bom = await _bom(db, material)
    inputs = sorted({i for s in samples for i in s["inputs"]} | set(bom))
    total = 0.0
    for inp in inputs:
        per_unit = [s["inputs"].get(inp, 0.0) / s["out"] for s in samples]
        med, kept, dropped = _robust_median(per_unit, p["mad_k"])
        b = bom.get(inp)
        usage = med if med is not None and kept else (b["qty_per"] if b else 0.0)
        made = (await db.execute(text(
            "select 1 from nc_cm_prod_costs where dr = 0 and material_code = :m and qty > 0 limit 1"),
            {"m": inp})).scalar()
        line = {"material_code": inp, "material_name": names.get(inp) or await dx._material_name(db, inp),
                "std_usage": usage, "usage_samples": len(per_unit), "outliers_dropped": dropped,
                "usage_min": min(per_unit) if per_unit else None,
                "usage_max": max(per_unit) if per_unit else None,
                "bom_qty_per": b["qty_per"] if b else None, "bom_uom": b["uom"] if b else None,
                "self_made": bool(made), "child": None}
        if b and b["qty_per"] and med is not None:
            line["bom_deviation"] = (med - b["qty_per"]) / b["qty_per"]
        if made and depth < MAX_DEPTH and inp != material:
            child = await standard_card(db, inp, period_from, period_to, p, depth=depth + 1, cache=cache)
            line["std_price"] = child.get("std_unit")
            line["price_basis"] = "its own standard (below)"
            line["child"] = child if child is not card else None
        else:
            pr = await _purchase_price(db, inp, period_to, int(p["window_months"]))
            line["std_price"] = pr["unit"] if pr else None
            line["price_basis"] = pr["basis"] if pr else "no price found"
            line["price_refs"] = pr["refs"] if pr else []
        line["std_cost"] = (line["std_price"] or 0.0) * usage
        total += line["std_cost"]
        card["lines"].append(line)
    card["lines"].sort(key=lambda l: -abs(l["std_cost"]))
    conv = await _conversion_rate(db, material, period_to, int(p["window_months"]), p["exclude_stoppage"])
    card["conversion"] = conv
    card["materials_unit"] = total
    card["std_unit"] = total + conv["unit"]
    return card


async def simulate(db: AsyncSession, card: dict, period_from: str, period_to: str) -> list[dict]:
    """Replay each month against the standard: where the actual moved and why."""
    std = {l["material_code"]: l for l in card["lines"]}
    rows = []
    pcs = await ca._product_cost(db, card["material_code"], period_from, period_to)
    for pc in pcs:
        q = pc["qty"] or 0
        if q <= 0:
            continue
        usage_var = price_var = input_var = 0.0
        worst_input = None
        for i in pc["inputs"]:
            if i["kind"] != "material" or not i["material_code"]:
                continue
            s = std.get(i["material_code"])
            sp = (s or {}).get("std_price") or 0.0
            su = (s or {}).get("std_usage") or 0.0
            aq = i["consumed_qty"] or 0.0
            usage_var += (aq - su * q) * sp
            diff = (i["consumed_amount"] or 0.0) - aq * sp
            if s and s.get("self_made"):
                # a self-made input that cost more or less than its own standard
                input_var += diff
                if worst_input is None or abs(diff) > abs(worst_input["variance"]):
                    worst_input = {"material_code": i["material_code"], "variance": diff,
                                   "actual_unit": i["consumed_unit"], "std_price": sp}
            else:
                price_var += diff
        conv_var = pc["conversion_total"] - card["conversion"]["unit"] * q
        adj = pc["settlement_total"] + pc["tail_total"] + pc["carried_total"]
        std_cost = card["std_unit"] * q
        residual = pc["amount"] - std_cost - usage_var - price_var - input_var - conv_var - adj
        rows.append({"period": pc["period"], "qty": q, "actual": pc["amount"], "actual_unit": pc["unit"],
                     "standard": std_cost, "std_unit": card["std_unit"],
                     "usage_variance": usage_var, "price_variance": price_var,
                     "input_cost_variance": input_var, "worst_input": worst_input,
                     "conversion_variance": conv_var, "adjustments": adj,
                     "settlement": pc["settlement_total"], "tail": pc["tail_total"],
                     "carried": pc["carried_total"], "unexplained": residual})
    return rows


def summarise(card: dict, sim: list[dict], params: dict) -> dict:
    m = card["material_code"]
    lead = (f"Suggested standard cost for {m}: {dx.u(card['std_unit'])}/unit — materials "
            f"{dx.u(card['materials_unit'])}, conversion {dx.u(card['conversion']['unit'])} "
            f"(usage from {card['samples']} {card['usage_basis']}).")
    because, bom_flags = [], []
    if sim:
        units = [r["actual_unit"] for r in sim if r["actual_unit"] is not None]
        because.append(f"In {sim[0]['period']}–{sim[-1]['period']} NC's actual cost ranged "
                       f"{dx.u(min(units))}–{dx.u(max(units))}/unit across {len(sim)} producing month(s).")
        ranked = sorted(sim, key=lambda r: -abs(r["actual"] - r["standard"]))
        for r in ranked[:2]:
            gap = r["actual"] - r["standard"]
            if abs(gap) < max(0.05 * abs(r["standard"]), 1):
                continue
            comps = [("usage", r["usage_variance"]), ("price", r["price_variance"]),
                     ("input", r["input_cost_variance"]), ("conversion", r["conversion_variance"]),
                     ("adjustments", r["adjustments"])]
            comps = [c for c in comps if abs(c[1]) >= max(0.1 * abs(gap), 1)]
            comps.sort(key=lambda c: -abs(c[1]))
            words = []
            for k, v in comps:
                if k == "usage":
                    words.append(f"{dx.m(v)} from using {'more' if v > 0 else 'less'} than the "
                                 f"standard quantities")
                elif k == "price":
                    words.append(f"{dx.m(v)} from purchased inputs priced "
                                 f"{'above' if v > 0 else 'below'} standard")
                elif k == "input":
                    wi = r.get("worst_input") or {}
                    words.append(f"{dx.m(v)} from in-house inputs costing "
                                 f"{'more' if v > 0 else 'less'} than their own standard"
                                 + (f" ({wi['material_code']} consumed at {dx.u(wi['actual_unit'])}/unit "
                                    f"against a standard {dx.u(wi['std_price'])} — open its card)"
                                    if wi.get("material_code") else ""))
                elif k == "conversion":
                    words.append(f"{dx.m(v)} of conversion cost {'above' if v > 0 else 'below'} "
                                 f"the standard rate")
                else:
                    parts = []
                    if abs(r["settlement"]) >= 0.01:
                        parts.append(f"{dx.m(r['settlement'])} settlement variances (a purchase price "
                                     f"variance on the inputs under standard costing)")
                    if abs(r["tail"]) >= 0.01:
                        parts.append(f"{dx.m(r['tail'])} month-end tails (would not arise)")
                    if abs(r["carried"]) >= 0.01:
                        parts.append(f"{dx.m(r['carried'])} carried in (stays in work in progress)")
                    words.append(f"{dx.m(v)} of accounting adjustments — " + ", ".join(parts))
            because.append(
                f"{r['period']}: actual {dx.u(r['actual_unit'])}/unit against the standard "
                f"{dx.u(r['std_unit'])} ({dx.m(gap)} on {dx.q(r['qty'])}): " + "; ".join(words)
                + ". Under standard costing the product would have cost the standard, the rest "
                  "going to variance accounts.")
    not_used, not_in_bom = [], []
    for l in card["lines"]:
        dev = l.get("bom_deviation")
        if l["bom_qty_per"] and not l["usage_max"]:
            not_used.append(l["material_code"])
            bom_flags.append({"material_code": l["material_code"], "bom": l["bom_qty_per"],
                              "actual_median": 0.0, "deviation": None, "uom": l["bom_uom"],
                              "kind": "in the BOM, never consumed"})
        elif l["bom_qty_per"] is None and l["std_usage"]:
            not_in_bom.append(l["material_code"])
            bom_flags.append({"material_code": l["material_code"], "bom": None,
                              "actual_median": l["std_usage"], "deviation": None, "uom": None,
                              "kind": "consumed, not in the BOM"})
        elif dev is not None and abs(dev) > params["bom_tolerance"]:
            bom_flags.append({"material_code": l["material_code"], "bom": l["bom_qty_per"],
                              "actual_median": l["std_usage"], "deviation": dev, "uom": l["bom_uom"],
                              "kind": "quantity differs"})
    qd = sorted([f for f in bom_flags if f["kind"] == "quantity differs"],
                key=lambda f: -abs(f["deviation"]))
    if qd:
        because.append("BOM vs actual usage: " + "; ".join(
            f"{f['material_code']} {f['bom']:.4f} in the BOM, {f['actual_median']:.4f} used "
            f"({f['deviation'] * 100:+.0f}%)" for f in qd[:3])
            + (f" — {len(qd)} line(s) beyond ±{params['bom_tolerance'] * 100:.0f}%." if len(qd) > 3 else "."))
    if not_used and not_in_bom:
        because.append(f"The BOM does not describe what is really consumed: {', '.join(not_used)} "
                       f"in the BOM but never consumed; {', '.join(not_in_bom)} consumed but not in "
                       f"the BOM. The standard uses actual consumption.")
    elif not_used:
        because.append(f"In the BOM but never consumed in the period: {', '.join(not_used)}.")
    elif not_in_bom:
        because.append(f"Consumed but not in the BOM: {', '.join(not_in_bom)}.")
    because.append("Conversion is suggested on NC's own basis — spread per unit of output, stoppage "
                   "left out. Whether a standard should use that basis (or hours, or a capacity "
                   "volume) is for Finance to decide.")
    return {"headline": lead, "because": because, "bom_suggestions": bom_flags}


async def standard_cost(db: AsyncSession, material: str, period_from: str, period_to: str,
                        params: dict | None = None) -> dict:
    p = {**DEFAULTS, **(params or {})}
    card = await standard_card(db, material, period_from, period_to, p)
    sim = await simulate(db, card, period_from, period_to)
    return {"params": p, "card": card, "simulation": sim, "summary": summarise(card, sim, p)}
