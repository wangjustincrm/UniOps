"""AI Financial Analysis P2 — gross margin and the cost chain.

Spec §6.5 and §8 (Overview, Cost Chain). Same rule as the diagnosis: the
conclusion first, the figures as evidence.

Margin is computed invoice by invoice, not month against month: every cost-of-
sales line (I5) in NC names the sales invoice it came from (src_code), and on
the 2026 mirror all 217 invoice × material groups pair with their invoice in
the same month and for the same quantity (dev1, 2026-10-09). Revenue is the
invoice line's local-currency amount excluding tax (decision D5). Issue
adjustments posted onto a cost-of-sales line belong to that sale's cost.

The cost chain walks NC's own product cost (CM_PRODCOST/_B) from a finished
good down through every self-made input to purchased materials, one node per
material, using the period the input was produced in (the same month, else the
latest earlier one — production crosses month-ends). That period choice is an
approximation of which batch was consumed and the response says so.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import cost_analysis as ca
from app.services import cost_diagnosis as dx

# Spec §11 Q3 default: by-products stay in the matrix, labelled.
BYPRODUCTS = {"CF0033": "cream", "CF00AF": "animal feed"}
# A month's margin is worth explaining when it moves this many points against
# the product's previous selling month, or goes negative.
MARGIN_SWING_POINTS = 10.0
CHAIN_DEPTH = 6

D0 = Decimal("0")


def _f(v):
    return None if v is None else float(v)


async def margin_matrix(db: AsyncSession, period_from: str, period_to: str) -> dict:
    """Product × month: quantity, revenue, cost of sales, margin."""
    rev = (await db.execute(text(
        "select material_code, max(material_name), to_char(bill_date, 'YYYY-MM'), "
        "invoice_code, coalesce(sum(qty),0), coalesce(sum(amount),0) "
        "from nc_so_invoice_lines where dr = 0 and to_char(bill_date, 'YYYY-MM') between :a and :b "
        "group by material_code, to_char(bill_date, 'YYYY-MM'), invoice_code"),
        {"a": period_from, "b": period_to})).all()
    cogs = (await db.execute(text(
        "select material_code, max(material_name), period, src_code, bill_code, "
        "coalesce(sum(qty),0), coalesce(sum(amount),0) from nc_ia_ledger "
        "where dr = 0 and bill_type = 'I5' and period between :a and :b "
        "group by material_code, period, src_code, bill_code"),
        {"a": period_from, "b": period_to})).all()
    # issue adjustments sitting on a cost-of-sales bill
    adj = (await db.execute(text(
        "select src_code, coalesce(sum(amount),0) from nc_ia_ledger "
        "where dr = 0 and bill_type = 'IA' and src_type = 'I5' and period between :a and :b "
        "group by src_code"), {"a": period_from, "b": period_to})).all()
    adj_by_bill = {b: Decimal(str(a)) for b, a in adj}

    cells: dict = defaultdict(lambda: {"qty": D0, "revenue": D0, "cost": D0, "cost_qty": D0,
                                       "invoices": set(), "unpaired": []})
    names: dict = {}
    rev_keys = set()
    for mat, name, per, inv, q, a in rev:
        c = cells[(mat, per)]
        c["qty"] += Decimal(str(q))
        c["revenue"] += Decimal(str(a))
        c["invoices"].add(inv)
        names[mat] = name
        rev_keys.add((mat, inv))
    cost_keys = set()
    for mat, name, per, inv, bill, q, a in cogs:
        names.setdefault(mat, name)
        c = cells[(mat, per)]
        c["cost"] += Decimal(str(a)) + adj_by_bill.get(bill, D0)
        c["cost_qty"] += Decimal(str(q))
        cost_keys.add((mat, inv))
    # A7: revenue without cost of sales, or the reverse — reported, not absorbed
    a7 = ([{"material_code": m, "invoice": i, "side": "revenue without cost of sales"}
           for m, i in sorted(rev_keys - cost_keys)]
          + [{"material_code": m, "invoice": i, "side": "cost of sales without revenue"}
             for m, i in sorted(cost_keys - rev_keys)])

    periods = ca.periods_between(period_from, period_to)
    rows = []
    for mat in sorted({m for m, _ in cells}):
        months = {}
        for p in periods:
            c = cells.get((mat, p))
            if not c or (c["qty"] == 0 and c["revenue"] == 0 and c["cost"] == 0):
                continue
            margin = c["revenue"] - c["cost"]
            months[p] = {
                "qty": _f(c["qty"]), "revenue": _f(c["revenue"]), "cost": _f(c["cost"]),
                "unit_price": _f(ca._unit(c["revenue"], c["qty"])),
                "unit_cost": _f(ca._unit(c["cost"], c["cost_qty"] or c["qty"])),
                "margin": _f(margin),
                "margin_pct": _f((margin / c["revenue"] * 100).quantize(Decimal("0.1")))
                              if c["revenue"] else None,
                "invoices": sorted(c["invoices"]),
            }
        if not months:
            continue
        tot_r = sum(Decimal(str(m["revenue"])) for m in months.values())
        tot_c = sum(Decimal(str(m["cost"])) for m in months.values())
        rows.append({
            "material_code": mat, "material_name": names.get(mat),
            "byproduct": BYPRODUCTS.get(mat),
            "months": months,
            "total": {"revenue": _f(tot_r), "cost": _f(tot_c), "margin": _f(tot_r - tot_c),
                      "margin_pct": _f(((tot_r - tot_c) / tot_r * 100).quantize(Decimal("0.1")))
                                    if tot_r else None},
        })
    rows.sort(key=lambda r: -(r["total"]["revenue"] or 0))
    return {"periods": periods, "rows": rows, "a7": a7}


async def margin_summary(db: AsyncSession, matrix: dict) -> dict:
    """Which product-months need explaining, and why — via the cost diagnosis."""
    findings = []
    for r in matrix["rows"]:
        prev = None
        for p in matrix["periods"]:
            cur = r["months"].get(p)
            if not cur or cur["margin_pct"] is None:
                continue
            why = None
            if cur["margin_pct"] < 0:
                why = f"negative margin {cur['margin_pct']}%"
            elif prev and prev[1]["margin_pct"] is not None and \
                    abs(cur["margin_pct"] - prev[1]["margin_pct"]) >= MARGIN_SWING_POINTS:
                why = (f"margin {prev[1]['margin_pct']}% in {prev[0]} → {cur['margin_pct']}%")
            if why:
                findings.append(await _explain_margin(db, r, p, cur, prev, why))
            prev = (p, cur)
    findings.sort(key=lambda f: (f["severity"] != "high", -(abs(f["swing"]) if f["swing"] else 0)))
    if findings:
        top = findings[0]
        headline = (f"{len(findings)} product-month(s) need attention. Largest: "
                    f"{top['material_code']} {top['period']} — {top['title']}.")
    else:
        headline = "Gross margin is steady: no product moved more than " \
                   f"{MARGIN_SWING_POINTS:.0f} points or went negative."
    return {"headline": headline, "findings": findings}


async def _explain_margin(db, row, period, cur, prev, why) -> dict:
    mat = row["material_code"]
    lead = (f"{mat} sold {dx.q(cur['qty'])} in {period} for {dx.m(cur['revenue'])} "
            f"({dx.u(cur['unit_price'])}/unit) at a cost of {dx.m(cur['cost'])} "
            f"({dx.u(cur['unit_cost'])}/unit): margin {dx.m(cur['margin'])}, {cur['margin_pct']}%.")
    because, evidence, related, root = [], [], [], []
    swing = None
    if prev:
        pc = prev[1]
        swing = (cur["margin_pct"] or 0) - (pc["margin_pct"] or 0)
        def moved(k):
            a, b = pc.get(k), cur.get(k)
            return a not in (None, 0) and b is not None and abs(b - a) / abs(a) >= 0.05
        price_moved, cost_moved = moved("unit_price"), moved("unit_cost")
        if cost_moved:
            because.append(
                f"Unit cost of sales moved from {dx.u(pc['unit_cost'])} in {prev[0]} to "
                f"{dx.u(cur['unit_cost'])} — the cost of the stock sold changed.")
        if price_moved:
            because.append(
                f"Unit selling price moved from {dx.u(pc['unit_price'])} in {prev[0]} to "
                f"{dx.u(cur['unit_price'])}.")
        if not cost_moved and not price_moved:
            because.append("Neither the unit price nor the unit cost moved by 5% or more; "
                           "the mix of invoices changed.")
        evidence.append(dx._ev(f"{mat} {prev[0]}", f"{dx.q(pc['qty'])} at {dx.u(pc['unit_price'])} "
                               f"/ cost {dx.u(pc['unit_cost'])} = margin {pc['margin_pct']}%"))
    evidence.append(dx._ev(f"{mat} {period}", f"{dx.q(cur['qty'])} at {dx.u(cur['unit_price'])} "
                           f"/ cost {dx.u(cur['unit_cost'])} = margin {cur['margin_pct']}%",
                           ", ".join(cur["invoices"][:4])))
    # Where the sold stock's value came from, when it is out of line.
    src = await dx.stock_source_cause(db, mat, period, cur["unit_cost"])
    if src:
        because.append(src["sentence"])
        evidence += src["evidence"]
    # What a unit of it is made of — the latest production month up to now.
    made = await _produced_in(db, mat, period)
    comp = await dx.cost_composition(db, mat, made) if made else None
    if comp and ((cur["margin_pct"] or 0) < 0 or comp["conversion_share"] >= 0.5):
        because.append(comp["sentence"])
        evidence += comp["evidence"]
        if cur["unit_price"] is not None and comp["unit"] and comp["unit"] > cur["unit_price"]:
            because.append(f"At {dx.u(cur['unit_price'])}/unit the selling price is below the "
                           f"{dx.u(comp['unit'])}/unit production cost of {made}.")
    # The cost of what was sold comes from production — ask the cost diagnosis
    # about the product over the months that stock could have been made in.
    d = await dx.diagnose(db, mat, ca.prev_period(ca.prev_period(period)), period)
    cost_f = [f for f in d["findings"] if f["kind"] == "cost"]
    if cost_f:
        f = sorted(cost_f, key=lambda f: (f["severity"] != "high", f["period"]))[0]
        because.append(f"Production: {f['conclusion']}")
        root = d.get("root_chain") or []
        if root:
            because.append(f"Root cause upstream: {root[-1]['headline']}.")
        related = sorted(set(f["related"]) | {h["material"] for h in root})
    return {
        "material_code": mat, "material_name": row["material_name"], "period": period,
        "severity": "high" if (cur["margin_pct"] or 0) < 0 or (swing is not None and abs(swing) >= 25) else "medium",
        "title": why, "conclusion": lead, "because": because, "evidence": evidence,
        "related": related, "root_chain": root, "swing": swing,
        "byproduct": row["byproduct"],
    }


# ── cost chain ───────────────────────────────────────────────────────────────

async def _produced_in(db: AsyncSession, material: str, period: str,
                       consumed_unit: float | None = None) -> str | None:
    """The month this material's consumed stock was most likely produced in.

    Without a consumed unit cost: the same month if it produced then, else the
    latest earlier one. With one: of the last three producing months, the one
    whose unit cost is closest to what was consumed — CS0080 used CR0059 at
    -1.2112 in September, which is August's batch, not September's 0.3871."""
    rows = (await db.execute(text(
        "select period, sum(amount) / nullif(sum(qty), 0) from nc_cm_prod_costs "
        "where dr = 0 and material_code = :m and period <= :p and qty > 0 "
        "group by period order by period desc limit 3"), {"m": material, "p": period})).all()
    if not rows:
        return None
    if consumed_unit is None:
        return rows[0][0]
    return min(rows, key=lambda r: abs(float(r[1] or 0) - consumed_unit))[0]


async def cost_chain(db: AsyncSession, material: str, period: str) -> dict:
    th = await ca.load_thresholds(db)
    seen: set = set()

    async def node(mat: str, per: str, depth: int, consumed: dict | None) -> dict:
        pcs = await ca._product_cost(db, mat, per, per)
        n = {"material_code": mat, "period": per, "consumed": consumed, "children": [],
             "made_in_house": bool(pcs)}
        if not pcs:
            n["material_name"] = await dx._material_name(db, mat)
            return n
        pc = pcs[0]
        n.update({
            "material_name": (await dx._material_name(db, mat)),
            "qty": pc["qty"], "amount": pc["amount"], "unit": pc["unit"],
            "breakdown": {k: pc[k] for k in ("consumed_total", "conversion_total",
                                             "settlement_total", "tail_total", "carried_total")},
        })
        hist = await dx._prod_history(db, mat)
        prev = [(p, q, a) for p, q, a in hist if p < per and a > 0]
        prev_u = prev[-1][2] / prev[-1][1] if prev else None
        n["previous_unit"] = prev_u
        n["previous_period"] = prev[-1][0] if prev else None
        n["abnormal"] = (pc["unit"] is not None and (pc["unit"] <= 0 or (
            prev_u and abs((pc["unit"] - prev_u) / prev_u) > th["a2_unit_cost_change"])))
        if depth >= CHAIN_DEPTH or (mat, per) in seen:
            n["truncated"] = True
            return n
        seen.add((mat, per))
        for inp in pc["inputs"]:
            if inp["kind"] != "material" or not inp["material_code"]:
                continue
            # What the input really cost this product: the issue plus any settlement
            # or tail adjustment NC posted onto it. CW0005 took CS0080 at 8.8002 on
            # the issue, but -2.4820 after the tail dump — September's batch.
            eff = None
            if inp["consumed_qty"]:
                eff = ((inp["consumed_amount"] or 0) + (inp["settlement_adj"] or 0)
                       + (inp["tail_adj"] or 0)) / inp["consumed_qty"]
            made = await _produced_in(db, inp["material_code"], per, eff)
            cons = {"qty": inp["consumed_qty"], "amount": inp["consumed_amount"],
                    "unit": inp["consumed_unit"], "settlement_adj": inp["settlement_adj"],
                    "effective_unit": eff,
                    "tail_adj": inp["tail_adj"], "element": inp["element_name"],
                    "share": (inp["nc_amount"] / pc["amount"]) if pc["amount"] else None}
            if made:
                n["children"].append(await node(inp["material_code"], made, depth + 1, cons))
            else:
                n["children"].append({"material_code": inp["material_code"],
                                       "material_name": inp["material_name"], "period": per,
                                       "consumed": cons, "children": [], "made_in_house": False})
        n["children"].sort(key=lambda c: -abs((c["consumed"] or {}).get("amount") or 0))
        return n

    root = await node(material, await _produced_in(db, material, period) or period, 0, None)
    abnormal = []

    def walk(n, path):
        if n.get("abnormal"):
            abnormal.append({"material_code": n["material_code"], "period": n["period"],
                             "unit": n.get("unit"), "path": path + [n["material_code"]]})
        for c in n["children"]:
            walk(c, path + [n["material_code"]])
    walk(root, [])
    # the deepest abnormal node on a path is where the problem starts
    deepest = max(abnormal, key=lambda a: len(a["path"]), default=None)
    if deepest and deepest["material_code"] != material:
        d = await dx.diagnose(db, deepest["material_code"], deepest["period"], deepest["period"])
        headline = (f"{material}'s cost chain has {len(abnormal)} abnormal node(s); the deepest is "
                    f"{deepest['material_code']} in {deepest['period']} "
                    f"({' ← '.join(reversed(deepest['path']))}). {d['headline']}")
    elif deepest:
        headline = f"{material} itself is abnormal in {deepest['period']}; its inputs are not."
    else:
        headline = f"{material}'s cost chain in {root['period']} has no abnormal node."
    return {"material": material, "period": root["period"], "headline": headline,
            "abnormal": abnormal, "tree": root,
            "note": "Each self-made input is shown at the month it was most likely produced in: "
                    "of its last three producing months, the one whose unit cost matches what "
                    "was consumed — an approximation of which batch was used; batch-level "
                    "tracing is phase P4."}
