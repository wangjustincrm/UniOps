"""AI Financial Analysis P3 — cost composition, drilled to the bottom (spec §6.4).

"Of the cost of a can, how much is milk, ingredients, packaging, payroll,
overhead, utilities — and how much is accounting adjustment?" NC answers this
for one level only: its product cost lists the inputs a product consumed, and
an in-house input is one line whatever it was made of. Here every in-house
input is expanded in the proportions of ITS OWN product cost, recursively, down
to purchased materials and conversion cost.

  * The month of an in-house input is the one its consumption matches
    (cost_margin._produced_in, by what the input really cost incl. settlement
    and tail adjustments) — production crosses month-ends.
  * Expansion is proportional, so every level adds up to NC's figure for it and
    the top adds up to NC's product cost exactly. What cannot be expanded (an
    in-house input with no product cost to read) stays as "Unattributable" and
    is flagged above 1% (rule A9) — never spread over the rest.
  * The result is an ANALYTICAL figure built from NC's input relationships,
    not a ledger figure, and the response says so.

Buckets: purchased materials by NC cost element (raw milk & components,
ingredients, packaging, milk powder); conversion by element group (payroll,
manufacturing overhead, utilities, stoppage); accounting adjustments
(settlement variances, month-end tails, carried in, and the issue price
difference: what NC charged for an in-house input minus that input's own cost
for the units consumed — NC issues at a running price, not at the batch's).

In-house inputs expand by quantity (units consumed × the input's own per-unit
composition), never by value: a value ratio flips signs when an input is
issued at a price of the other sign to its own cost.
"""
from __future__ import annotations

from collections import defaultdict

from app.services import cost_analysis as ca
from app.services import cost_diagnosis as dx
from app.services import cost_margin as mg

MAX_DEPTH = 8
A9_SHARE = 0.01

MATERIAL_BUCKETS = {
    "100101": "Raw milk & milk components",
    "100102": "Raw milk & milk components",     # pasteurised milk bought in (Lactalis)
    "100103": "Ingredients",
    "100105": "Milk powder",
    "100106": "Packaging",
}
PAYROLL = {f"51{n:02d}" for n in range(2, 11)} | {"5114", "5115"}
UTILITIES = {"5111", "5112", "5113"}
STOPPAGE = {"5116", "5117"}

ORDER = ["Raw milk & milk components", "Milk powder", "Ingredients", "Packaging",
         "Other materials", "Payroll", "Manufacturing overhead", "Utilities", "Stoppage",
         "Settlement variances", "Month-end tails", "Carried in", "Issue price difference",
         "Unattributable"]
GROUP = {
    "Raw milk & milk components": "materials", "Milk powder": "materials", "Ingredients": "materials",
    "Packaging": "materials", "Other materials": "materials",
    "Payroll": "conversion", "Manufacturing overhead": "conversion", "Utilities": "conversion",
    "Stoppage": "conversion",
    "Settlement variances": "adjustments", "Month-end tails": "adjustments", "Carried in": "adjustments",
    "Issue price difference": "adjustments",
    "Unattributable": "unattributable",
}


def _conversion_bucket(code: str | None) -> str:
    if code in PAYROLL:
        return "Payroll"
    if code in UTILITIES:
        return "Utilities"
    if code in STOPPAGE:
        return "Stoppage"
    return "Manufacturing overhead"


async def _compose(db, material: str, period: str, depth: int, cache: dict, path: tuple) -> dict | None:
    """Buckets (summing to NC's product cost of material@period) and the
    purchased-material leaves behind them."""
    key = (material, period)
    if key in cache:
        return cache[key]
    pcs = await ca._product_cost(db, material, period, period)
    if not pcs:
        return None
    pc = pcs[0]
    buckets: dict = defaultdict(float)
    leaves: dict = defaultdict(lambda: {"amount": 0.0, "qty": 0.0, "name": None, "bucket": None})
    via: list = []
    for i in pc["inputs"]:
        if i["kind"] == "conversion":
            b = _conversion_bucket(i["element_code"])
            buckets[b] += (i["conversion"] or 0.0)
            buckets["Carried in"] += (i["carried_in"] or 0.0)
            continue
        buckets["Settlement variances"] += i["settlement_adj"] or 0.0
        buckets["Month-end tails"] += i["tail_adj"] or 0.0
        buckets["Carried in"] += i["carried_in"] or 0.0
        consumed = i["consumed_amount"] or 0.0
        if not consumed:
            continue
        code = i["material_code"]
        child = None
        if depth < MAX_DEPTH and code not in path:
            eff = None
            if i["consumed_qty"]:
                eff = (consumed + (i["settlement_adj"] or 0) + (i["tail_adj"] or 0)) / i["consumed_qty"]
            made = await mg._produced_in(db, code, period, eff)
            if made:
                child = await _compose(db, code, made, depth + 1, cache, path + (material,))
                if child is None:
                    buckets["Unattributable"] += consumed
                    continue
        if child is not None:
            # Expand by QUANTITY: the units consumed carry the input's own
            # per-unit composition. Scaling by value breaks exactly when it
            # matters — CW0005 took CS0080 at +47,805 (an old issue price)
            # while CS0080's own cost was -13,483; a value ratio of -3.5 would
            # flip every bucket. Whatever the issue price differs from the
            # input's own cost is its own bucket.
            cq = i["consumed_qty"] or 0.0
            if not child["qty"] or not cq:
                buckets["Unattributable"] += consumed
                continue
            scale = cq / child["qty"]
            for b, v in child["buckets"].items():
                buckets[b] += v * scale
            for lc, lv in child["leaves"].items():
                lf = leaves[lc]
                lf["amount"] += lv["amount"] * scale
                lf["qty"] += lv["qty"] * scale
                lf["name"], lf["bucket"] = lv["name"], lv["bucket"]
            gap = consumed - child["total"] * scale
            buckets["Issue price difference"] += gap
            via.append({"material_code": code, "material_name": i["material_name"],
                        "period": child["period"], "consumed": consumed, "qty": cq,
                        "own_cost": child["total"] * scale, "issue_price_difference": gap})
        else:
            b = MATERIAL_BUCKETS.get(i["element_code"], "Other materials")
            buckets[b] += consumed
            lf = leaves[code]
            lf["amount"] += consumed
            lf["qty"] += i["consumed_qty"] or 0.0
            lf["name"], lf["bucket"] = i["material_name"], b
    out = {"material_code": material, "period": period, "qty": pc["qty"], "total": pc["amount"],
           "buckets": dict(buckets), "leaves": dict(leaves), "via": via}
    cache[key] = out
    return out


async def composition(db, material: str, period: str) -> dict:
    made = await mg._produced_in(db, material, period) or period
    cache: dict = {}
    c = await _compose(db, material, made, 0, cache, ())
    if c is None:
        return {"material": material, "period": made, "available": False,
                "headline": f"{material} has no NC product cost in or before {period}."}
    q = c["qty"] or 0.0
    total = c["total"]
    rows = []
    for b in ORDER:
        v = c["buckets"].get(b, 0.0)
        if abs(v) < 0.005:
            continue
        rows.append({"bucket": b, "group": GROUP[b], "amount": v, "per_unit": v / q if q else None,
                     "share": v / total if total else None})
    tie = total - sum(r["amount"] for r in rows)
    groups: dict = defaultdict(float)
    for r in rows:
        groups[r["group"]] += r["amount"]
    leaves = sorted(({"material_code": k, "material_name": v["name"], "bucket": v["bucket"],
                      "amount": v["amount"], "qty": v["qty"],
                      "per_unit_of_product": v["amount"] / q if q else None}
                     for k, v in c["leaves"].items() if abs(v["amount"]) >= 0.005),
                    key=lambda x: -abs(x["amount"]))

    # Compare with the latest earlier month the product cost was positive
    hist = await dx._prod_history(db, material)
    prev = [p for p, qq, aa in hist if p < made and aa > 0]
    compare = None
    if prev:
        pc_ = await _compose(db, material, prev[-1], 0, {}, ())
        if pc_ and pc_["qty"]:
            pq = pc_["qty"]
            compare = {"period": pc_["period"], "per_unit": {b: v / pq for b, v in pc_["buckets"].items()},
                       "unit": pc_["total"] / pq}
    unattr = c["buckets"].get("Unattributable", 0.0)
    a9 = abs(total) > 0.005 and abs(unattr) / abs(total) > A9_SHARE

    def pu(v):
        return v / q if q else None
    lead = (f"{material}'s {made} cost of {dx.m(total)} ({dx.u(pu(total))}/unit) is "
            + ", ".join(f"{g} {dx.u(pu(v))}/unit" for g, v in
                        sorted(groups.items(), key=lambda kv: ["materials", "conversion", "adjustments",
                                                               "unattributable"].index(kv[0]))
                        if abs(v) >= 0.005) + ".")
    because = []
    adj = groups.get("adjustments", 0.0)
    if abs(adj) >= max(0.05 * abs(total), 1):
        parts = [f"{r['bucket'].lower()} {dx.m(r['amount'])}" for r in rows if r["group"] == "adjustments"]
        because.append(
            f"{dx.m(adj)} of it is accounting adjustment ({', '.join(parts)}), carried up through "
            f"{len(c['via'])} in-house input(s). Without it the product would have cost "
            f"{dx.u(pu(total - adj))}/unit.")
    if compare:
        diffs = []
        for r in rows:
            before = compare["per_unit"].get(r["bucket"], 0.0)
            diffs.append((r["bucket"], (r["per_unit"] or 0) - before, before))
        for b, v in compare["per_unit"].items():
            if b not in {r["bucket"] for r in rows}:
                diffs.append((b, -v, v))
        diffs.sort(key=lambda d: -abs(d[1]))
        movers = [d for d in diffs if abs(d[1]) >= 0.02 * max(abs(compare["unit"]), 1e-9)][:3]
        if movers:
            because.append(
                f"Against {compare['period']} ({dx.u(compare['unit'])}/unit): "
                + "; ".join(f"{b} {dx.u(before)} → {dx.u(before + d)}" for b, d, before in movers) + ".")
    conv = groups.get("conversion", 0.0)
    if total and conv / total >= 0.5:
        because.append(f"Conversion cost is {dx.pct(conv / total)} of the product's cost — "
                       f"see Gross Margin for how NC spreads it by quantity.")
    if a9:
        because.append(f"A9: {dx.m(unattr)} ({dx.pct(abs(unattr) / abs(total))}) could not be "
                       f"traced to purchased materials or conversion — an in-house input with no "
                       f"product cost to read.")
    return {"material": material, "period": made, "available": True, "qty": q, "total": total,
            "unit": pu(total), "buckets": rows, "groups": dict(groups), "leaves": leaves[:25],
            "compare": compare, "tie_out": tie, "a9": a9,
            "headline": lead, "because": because,
            "note": "Analytical figure: each in-house input is expanded by the units consumed, at "
                    "its own NC product cost per unit, level by level; what NC charged beyond that "
                    "is the issue price difference. Every level adds up to NC's own figure."}
