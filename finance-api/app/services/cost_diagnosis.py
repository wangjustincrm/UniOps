"""AI Financial Analysis — diagnosis: the conclusion first, the data as evidence.

User, 2026-10-09: "your job is to analyse and summarise, then provide the data
to support it" — a page of numbers with the bad ones in red is no better than
opening NC. This module is the reasoning that turned the CR0059 numbers into an
explanation, written down as rules so it runs on any material:

  1. A month whose production cost is non-positive or swung hard is split into
     what NC's own collection says built it — consumed inputs, conversion
     cost, settlement adjustments (31), tail adjustments (60), carried in.
  2. Each component that drives the result is traced to its cause:
       settlement  → the input's provisional receipt and settlement, and
                     whether the provisional stock had already been consumed
       tail        → the input that hit zero quantity with value left
       upstream    → an input consumed at a non-positive unit cost (link)
       carried in  → cost collected in a month with no output
       volume      → the month's output against a typical month
  3. Stock left at month-end with negative value: carried into the next
     month, and who consumed it there (production crosses month-ends).
  4. A large tail adjustment on this material: why value was left over, and
     which production order — which product — absorbed it.

Every sentence is assembled from figures computed here from the mirror; no
number is produced by a model. Each finding carries its evidence (label,
value, NC document) so the reader can check the claim, and the chain lists the
steps in date order.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import cost_analysis as ca

SIGNIFICANT_SHARE = 0.05     # a component counts if it is ≥5% of the month's cost …
LOW_OUTPUT_SHARE = 0.25      # … and output is "small" below 25% of a typical month


def m(v) -> str:
    """Money: 2 dp, thousands separators, sign kept."""
    return "—" if v is None else f"{float(v):,.2f}"


def u(v) -> str:
    """Unit cost: 4 dp."""
    return "—" if v is None else f"{float(v):,.4f}"


def q(v) -> str:
    if v is None:
        return "—"
    f = float(v)
    return f"{f:,.0f}" if abs(f - round(f)) < 1e-9 else f"{f:,.3f}".rstrip("0").rstrip(".")


def pct(v) -> str:
    return f"{float(v) * 100:,.0f}%"


def _ev(label: str, value: str, ref: str | None = None) -> dict:
    return {"label": label, "value": value, "ref": ref}


async def _prod_history(db: AsyncSession, material: str) -> list[tuple[str, float, float]]:
    rows = (await db.execute(text(
        "select period, coalesce(sum(qty),0), coalesce(sum(amount),0) from nc_cm_prod_costs "
        "where dr = 0 and material_code = :m group by period order by period"),
        {"m": material})).all()
    return [(p, float(qty), float(a)) for p, qty, a in rows if float(qty) > 0]


async def _material_name(db: AsyncSession, code: str) -> str | None:
    return (await db.execute(text(
        "select max(material_name) from nc_ia_ledger where material_code = :m"),
        {"m": code})).scalar()


async def _settlement_cause(db, closed, product: str, period: str, inp: dict) -> dict | None:
    """Why a settlement adjustment landed on `product` in `period`."""
    code = inp["material_code"]
    rows = await ca._ledger(db, material=code)
    events = [e for e in ca.settlement_events(rows) if e["settlement_period"] == period]
    if not events:
        return None
    balances = await ca._balances(db, code)
    sentences, evidence, steps = [], [], []
    used_in: set[str] = set()
    for e in events:
        pv = e["provisional"]
        if not pv:
            sentences.append(
                f"{code} was settled on {e['settlement_code']} in {period} with a difference of "
                f"{m(e['variance'])}, but its provisional receipt could not be found in the mirror.")
            evidence.append(_ev(f"{code} settlement variance", m(e["variance"]), e["settlement_code"]))
            continue
        # Was the provisionally priced stock already used when the settlement came?
        roll = ca.roll_months(rows, balances, code, [pv["period"]], closed)
        left = roll[0]["_q"] if roll else None
        if left is not None and pv["qty"] and float(left) <= 0.01 * pv["qty"]:
            used = f"all of it had been consumed by the end of {pv['period']}"
        elif left is not None and pv["qty"]:
            used = (f"only {q(left)} of the {q(pv['qty'])} was still in stock at the end of "
                    f"{pv['period']}")
        else:
            used = "it had already been consumed"
        used_in.add(pv["period"])
        who = (await db.execute(text(
            "select cost_object_code, coalesce(sum(qty),0) from nc_cm_stuff_lines where dr = 0 "
            "and material_code = :m and period = :p and source_type = 18 "
            "group by cost_object_code order by 2 desc"), {"m": code, "p": pv["period"]})).all()
        by = ", ".join(f"{c} ({q(x)})" for c, x in who[:3])
        sentences.append(
            f"{code} {inp.get('material_name') or ''} was received in {pv['period']} on "
            f"{pv['bill_code']} at a provisional {u(pv['unit'])}/unit and settled in {period} on "
            f"{e['settlement_code']} at {u(e['settled']['unit'])}/unit — a difference of "
            f"{m(e['variance'])} on {q(e['settled']['qty'])}. By then {used}, so the difference "
            f"could not stay in inventory and NC charged {m(inp['settlement_adj'])} of it to "
            f"{product}'s {period} cost.".replace("  ", " "))
        evidence += [
            _ev(f"{code} provisional receipt ({pv['period']})",
                f"{q(pv['qty'])} at {u(pv['unit'])} = {m(pv['amount'])}", pv["bill_code"]),
            _ev(f"{code} settlement ({period})",
                f"{q(e['settled']['qty'])} at {u(e['settled']['unit'])} = {m(e['settled']['amount'])}",
                e["settlement_code"]),
            _ev(f"{code} variance charged to {product}", m(inp["settlement_adj"])),
        ]
        steps.append({"period": pv["period"],
                      "text": f"{code} received at a provisional {u(pv['unit'])}/unit "
                              f"({pv['bill_code']}) and consumed" + (f" by {by}" if by else "")})
        steps.append({"period": period,
                      "text": f"{code} settled at {u(e['settled']['unit'])}/unit "
                              f"({e['settlement_code']}); {m(inp['settlement_adj'])} charged to "
                              f"{product}'s {period} cost"})
    return {"sentences": sentences, "evidence": evidence, "steps": steps, "used_in": used_in}


async def _tail_cause(db, product: str, period: str, inp: dict) -> dict:
    code = inp["material_code"]
    rows = await ca._ledger(db, material=code)
    ias = [r for r in rows if r.period == period and r.category == "out_adjust"
           and abs(r.amount) >= Decimal("0.01") * abs(Decimal(str(inp["tail_adj"])))]
    refs = [r.bill_code for r in ias if r.bill_code]
    return {
        "sentences": [
            f"{code} {inp.get('material_name') or ''} reached zero quantity in {period} with value "
            f"left over, and NC's month-end tail adjustment put {m(inp['tail_adj'])} of it on "
            f"{product}. The cause lies in {code}'s own cost — open its trace.".replace("  ", " ")],
        "evidence": [_ev(f"Tail adjustment from {code}", m(inp["tail_adj"]),
                         ", ".join(refs) or None)],
        "steps": [{"period": period,
                   "text": f"Tail adjustment from {code} ({m(inp['tail_adj'])}) landed on {product}"}],
        "related": [code],
    }


async def _cost_finding(db, closed, th, material, name, pc, history) -> dict | None:
    t, qty, amount, unit = pc["period"], pc["qty"], pc["amount"], pc["unit"]
    # Compare with the last month whose own cost was positive — comparing with
    # an abnormal month reports its recovery as another anomaly.
    prev = [(p, qq, aa) for p, qq, aa in history if p < t and aa > 0]
    prev_p, prev_u = (prev[-1][0], prev[-1][2] / prev[-1][1]) if prev else (None, None)
    swing = (prev_u not in (None, 0) and unit is not None
             and abs((unit - prev_u) / abs(prev_u)) > th["a2_unit_cost_change"])
    if unit is None or not (unit <= 0 or swing):
        return None

    base = max(abs(amount or 0), abs(pc["consumed_total"] or 0), 1.0)
    sig = lambda v: v is not None and abs(v) >= max(SIGNIFICANT_SHARE * base, 1.0)  # noqa: E731
    causes, evidence, steps, related = [], [], [], []
    evidence += [
        _ev(f"{material} output {t}", q(qty)),
        _ev(f"{material} cost {t}", f"{m(amount)} ({u(unit)}/unit)"),
        _ev("of which inputs consumed", m(pc["consumed_total"])),
    ]
    if abs(pc["conversion_total"]) >= 0.01:
        evidence.append(_ev("of which conversion cost", m(pc["conversion_total"])))
    if abs(pc["settlement_total"]) >= 0.01:
        evidence.append(_ev("of which settlement adjustments", m(pc["settlement_total"])))
    if abs(pc["tail_total"]) >= 0.01:
        evidence.append(_ev("of which tail adjustments", m(pc["tail_total"])))
    if abs(pc["carried_total"]) >= 0.01:
        evidence.append(_ev("of which carried in from earlier months", m(pc["carried_total"])))

    drivers: list[tuple[float, str, str]] = []   # (signed amount, kind, material) for the headline
    used_in: set[str] = set()
    for inp in pc["inputs"]:
        if inp["kind"] != "material":
            continue
        if sig(inp["settlement_adj"]):
            c = await _settlement_cause(db, closed, material, t, inp)
            if c:
                causes += c["sentences"]
                evidence += c["evidence"]
                steps += c["steps"]
                used_in |= c["used_in"]
                drivers.append((inp["settlement_adj"], "settlement", inp["material_code"]))
        if sig(inp["tail_adj"]):
            c = await _tail_cause(db, material, t, inp)
            causes += c["sentences"]
            evidence += c["evidence"]
            steps += c["steps"]
            related += c["related"]
            drivers.append((inp["tail_adj"], "tail", inp["material_code"]))
        cu = inp["consumed_unit"]
        if inp["consumed_qty"] and cu is not None and cu <= 0 and sig(inp["consumed_amount"]):
            causes.append(
                f"{inp['material_code']} {inp.get('material_name') or ''} was consumed at "
                f"{u(cu)}/unit ({m(inp['consumed_amount'])} for {q(inp['consumed_qty'])}) — its "
                f"own cost was already non-positive. Open its trace for why.".replace("  ", " "))
            evidence.append(_ev(f"{inp['material_code']} consumed",
                                f"{q(inp['consumed_qty'])} at {u(cu)} = {m(inp['consumed_amount'])}"))
            related.append(inp["material_code"])
            drivers.append((inp["consumed_amount"], "upstream", inp["material_code"]))
            steps.append({"period": t, "text": f"{material} consumed {inp['material_code']} at {u(cu)}/unit"})
    if sig(pc["carried_total"]):
        causes.append(f"{m(pc['carried_total'])} of cost collected in earlier months with no "
                      f"output was carried into {t}.")
        drivers.append((pc["carried_total"], "carried", ""))

    small = None
    settle = pc["settlement_total"]
    hist = {p: (qq, aa) for p, qq, aa in history}
    src = sorted(p for p in used_in if p in hist and p != t)
    if sig(settle) and src:
        sq = sum(hist[p][0] for p in src)
        sa = sum(hist[p][1] for p in src)
        share = qty / sq if sq else None
        without = (amount - settle) / qty if qty else None
        fixed = (sa + settle) / sq if sq else None
        months_txt = ", ".join(src)
        causes.append(
            f"The variance belongs to the stock {product_of(material)} consumed in {months_txt} — "
            f"{q(sq)} of output at {u(sa / sq)}/unit — but it was charged to {t}'s run of "
            f"{q(qty)}" + (f", {pct(share)} of that volume" if share is not None else "") +
            f". Without it, {t} would have cost {u(without)}/unit; charged to {months_txt}, "
            f"where the stock was used, {months_txt} would have been {u(fixed)}/unit instead of "
            f"{u(sa / sq)}.")
        evidence += [_ev(f"{material} output in {months_txt} (when the stock was consumed)",
                         f"{q(sq)} at {u(sa / sq)}/unit"),
                     _ev(f"{material} {t} unit cost without the variance", u(without)),
                     _ev(f"{material} {months_txt} unit cost with the variance", u(fixed))]
        if share is not None and share < 1:
            small = share
    others = [qq for p, qq, _ in history if p != t]
    if small is None and len(others) >= 2:
        med = statistics.median(others)
        if med > 0 and qty < LOW_OUTPUT_SHARE * med:
            small = qty / med
            causes.append(
                f"Output in {t} was only {q(qty)} — {pct(small)} of a typical month "
                f"({q(med)}) — so these amounts landed on a small run, which is why the unit "
                f"cost moved to {u(unit)}.")
            evidence.append(_ev(f"{material} typical monthly output (median)", q(med)))

    # The headline cause: the component pulling the result hardest in the
    # direction it moved.
    down = unit <= 0 or (prev_u is not None and unit < prev_u)
    by_kind: dict[str, list] = defaultdict(lambda: [0.0, []])
    for amt, kind_, code in drivers:
        if kind_ in ("tail", "upstream"):
            kind_ = "inherited"
        by_kind[kind_][0] += amt
        if code:
            by_kind[kind_][1].append(code)
    labels = {
        "settlement": lambda c: f"settlement variances on {', '.join(c)}",
        "inherited": lambda c: f"negative value inherited from {', '.join(dict.fromkeys(c))}",
        "carried": lambda c: "cost carried in from earlier months",
    }
    ranked = sorted(by_kind.items(), key=lambda kv: kv[1][0] if down else -kv[1][0])
    primary = (labels[ranked[0][0]](ranked[0][1][1]) + f" ({m(ranked[0][1][0])})") if ranked \
        else "a change in the inputs consumed"
    kind = "Non-positive" if unit <= 0 else "Swing in"
    title = f"{kind} production cost in {t}: {primary}" + (" landing on a small run" if small else "")
    lead = (f"{material}'s {t} production cost was {m(amount)} for {q(qty)} ({u(unit)}/unit"
            + (f", against {u(prev_u)}/unit in {prev_p})." if prev_u is not None else ").")
            + f" The main driver is {primary}" + (", spread over a much smaller output than the one it belongs to." if small else "."))
    steps.append({"period": t, "text": f"{material} output {q(qty)} → {u(unit)}/unit"})
    return {"kind": "cost", "period": t, "severity": "high" if unit <= 0 else "medium",
            "title": title, "conclusion": lead, "because": causes, "evidence": evidence,
            "steps": steps, "related": sorted(set(related))}


def product_of(material: str) -> str:
    return material


async def _carry_finding(db, material, month, rows) -> dict | None:
    cq, ca_ = month["_q"], month["_a"]
    if not (cq > ca.QTY_EPS and ca_ < -ca.AMT_EPS):
        return None
    t = month["period"]
    nxt = await ca._flows_next_month(db, rows, material, t)
    went = [f"{f['mo_code']} making " + (", ".join(p["material_code"] for p in f["products"]) or "?")
            + f" ({q(f['qty'])} at {m(f['amount'])})" for f in nxt[:5]]
    because = [f"In {ca.next_period(t)} it was consumed by: " + "; ".join(went) + "."] if went else \
        [f"Nothing has consumed it in {ca.next_period(t)} yet."]
    return {
        "kind": "carry", "period": t, "severity": "high",
        "title": f"Negative value carried out of {t}",
        "conclusion": (f"{material} ended {t} with {q(cq)} on hand valued at {m(ca_)} "
                       f"({u(ca_ / cq)}/unit). It was not consumed in {t}, so the "
                       f"negative value went into {ca.next_period(t)} with the stock — production "
                       f"crosses month-ends, and whatever consumes it inherits the value."),
        "because": because,
        "evidence": [_ev(f"{material} closing {t}", f"{q(cq)} valued at {m(ca_)}")]
                    + [_ev(f"Consumed in {f['period']} by {f['mo_code']}",
                           f"{q(f['qty'])} = {m(f['amount'])}") for f in nxt[:5]],
        "steps": [{"period": t, "text": f"{material} carried {q(cq)} at {m(ca_)} into "
                                        f"{ca.next_period(t)}"}],
        "related": sorted({p["material_code"] for f in nxt for p in f["products"]}),
    }


async def _tail_dump_finding(db, th, material, month, ia_rows, landing,
                             settlements) -> list | None:
    t = month["period"]
    big = [r for r in ia_rows if r.period == t
           and abs(r.amount) >= Decimal(str(th["a6_min_amount"]))]
    if not big:
        return None
    findings = []
    fl = month["flows"]
    out_q = sum((fl[k]["qty"] or 0) for k in ("consumption", "cogs", "transfer_out", "other_out") if k in fl)
    out_a = sum((fl[k]["amount"] or 0) for k in ("consumption", "cogs", "transfer_out", "other_out") if k in fl)
    issue_unit = out_a / out_q if out_q else None
    prod = fl.get("production")
    for r in big:
        lands = landing.get(r.nc_pk, [])
        target = ", ".join(f"{x['material_code']} {x.get('material_name') or ''}".strip() for x in lands) or "an unidentified consumer"
        evidence = [_ev(f"{material} opening {t}", f"{q(month['opening']['qty'])} valued at {m(month['opening']['amount'])}")]
        if prod:
            evidence.append(_ev(f"{material} produced in {t}",
                                f"{q(prod['qty'])} = {m(prod['amount'])}"
                                + (f" ({u(prod['amount'] / prod['qty'])}/unit)" if prod['qty'] else "")))
        if issue_unit is not None:
            evidence.append(_ev(f"{material} issued in {t} before adjustment",
                                f"{q(out_q)} = {m(out_a)} ({u(issue_unit)}/unit)"))
        evidence.append(_ev("Tail adjustment", m(r.amount), r.bill_code))
        vlist = (await ca.vouchers_for(db, {r.bill_code})).get(r.bill_code) or []
        for v in vlist:
            evidence.append(_ev(f"Voucher for {r.bill_code}",
                                f"{v['voucher_no']} ({v['period']}) — {v['status_label']}",
                                v["explanation"]))
        if not vlist:
            evidence.append(_ev(f"Voucher for {r.bill_code}", "none generated yet"))
        unposted = [v for v in vlist if v["status"] == "unposted"]
        vnote = (f" The voucher carrying it — {', '.join(v['voucher_no'] for v in unposted)} "
                 f"({unposted[0]['period']}) — is not posted yet, so a posted-only ledger does "
                 f"not show it." if unposted else
                 (f" No voucher has been generated for {r.bill_code} yet." if not vlist else ""))
        absorbed = None
        if lands:
            row = (await db.execute(text(
                "select coalesce(sum(qty),0), coalesce(sum(amount),0) from nc_cm_stuff_lines "
                "where dr = 0 and material_code = :m and period = :p and cost_object_code = any(:o) "
                "and source_type in (18, 31, 60)"),
                {"m": material, "p": t, "o": [x["material_code"] for x in lands]})).one()
            if float(row[0]) > 0:
                absorbed = (float(row[0]), float(row[1]))
                evidence.append(_ev(f"{target} took {material} in {t}",
                                    f"{q(absorbed[0])} = {m(absorbed[1])} "
                                    f"({u(absorbed[1] / absorbed[0])}/unit)"))
        if r.mo_code:
            o_q, o_a = month["opening"]["qty"], month["opening"]["amount"]
            in_q, in_a = month["receipts"]["qty"], month["receipts"]["amount"]
            left = o_a + in_a - out_a
            avail_q = o_q + in_q
            avail_u = (o_a + in_a) / avail_q if avail_q else None
            opening = (f"{material} came into {t} with {q(o_q)} valued at {m(o_a)}; "
                       if abs(o_a) >= 0.01 else "")
            why = (opening
                   + f"the stock available in {t} cost {u(avail_u)}/unit ({m(o_a + in_a)} for "
                     f"{q(avail_q)}), but it was issued at {u(issue_unit)}/unit on average "
                     f"({m(out_a)} for {q(out_q)}). "
                   + f"{m(o_a)} opening + {m(in_a)} received − {m(out_a)} issued = {m(left)} still "
                     f"on the books when quantity reached zero. NC's month-end run posted it "
                     f"({r.bill_code}, {m(r.amount)}) onto the last issue {r.src_code or ''} — "
                     f"production order {r.mo_code}, making {target}.")
            con = (f"In {t}, NC dumped {m(r.amount)} of {material}'s value on a single production "
                   f"order ({r.mo_code} → {target}) as a month-end tail adjustment"
                   + (f"; {target} ended up taking {material} at {u(absorbed[1] / absorbed[0])}/unit."
                      if absorbed else "."))
        else:
            ev = [e for e in settlements if e["settlement_period"] == t
                  and abs(e["_variance"] - r.amount) <= Decimal("0.05")]
            if ev and ev[0]["provisional"]:
                e = ev[0]
                pv = e["provisional"]
                why = (f"Settlement {e['settlement_code']} repriced the {pv['period']} provisional "
                       f"receipt {pv['bill_code']} from {u(pv['unit'])} to {u(e['settled']['unit'])}"
                       f"/unit after that stock had been consumed. The {m(e['variance'])} "
                       f"difference could not stay in inventory, so NC posted it as {r.bill_code} "
                       f"onto {target}, the cost object consuming {material} in {t}.")
                evidence.append(_ev("Settlement", m(e["variance"]), e["settlement_code"]))
                con = (f"In {t}, the settlement of {material}'s {pv['period']} provisional receipt "
                       f"moved {m(r.amount)} onto {target}.")
            else:
                why = (f"{m(r.amount)} was posted as {r.bill_code} onto {target} as an amount-only "
                       f"adjustment on {material}; no settlement in {t} matches it.")
                con = f"In {t}, {m(r.amount)} of {material}'s value was moved onto {target} after the fact."
        findings.append({
            "kind": "tail", "period": t, "severity": "high" if abs(r.amount) >= 10 * Decimal(str(th["a6_min_amount"])) else "medium",
            "title": f"{m(r.amount)} moved onto {target} in {t}",
            "conclusion": con, "because": [why + vnote], "evidence": evidence,
            "steps": [{"period": t, "text": f"{r.bill_code} {m(r.amount)} → {target}"}],
            "related": [x["material_code"] for x in lands],
        })
    return findings


async def _root(db: AsyncSession, code: str, period: str, depth: int, seen: set) -> list[dict]:
    """Follow inherited value upstream: the related material's own diagnosis
    over [period-1, period], and from its worst finding onward, until a
    finding has no upstream of its own (the root) or depth runs out."""
    if depth <= 0 or code in seen:
        return []
    seen.add(code)
    d = await diagnose(db, code, ca.prev_period(period), period, _depth=0)
    cost = [f for f in d["findings"] if f["kind"] == "cost"]
    if not cost:
        return [{"material": code, "period": period, "headline": d["headline"]}] if d["findings"] else []
    f = sorted(cost, key=lambda f: (f["severity"] != "high", f["period"]))[0]
    hop = {"material": code, "period": f["period"], "headline": f"{code}: {f['title']}",
           "conclusion": f["conclusion"]}
    nxt = [r for r in f["related"] if r not in seen]
    deeper = await _root(db, nxt[0], f["period"], depth - 1, seen) if nxt else []
    if not deeper and f["related"]:
        # The upstream material's abnormal month may be earlier than this one
        # (CR0059 went negative in August, was consumed in September).
        for r in f["related"]:
            if r in seen:
                continue
            deeper = await _root(db, r, ca.prev_period(f["period"]), depth - 1, seen)
            if deeper:
                break
    return [hop] + deeper


async def diagnose(db: AsyncSession, material: str, period_from: str, period_to: str,
                   trace: dict | None = None, _depth: int = 4) -> dict:
    th = await ca.load_thresholds(db)
    closed = await ca.closed_periods(db)
    if trace is None:
        trace = await ca.material_trace(db, material, period_from, period_to)
    name = trace["material"]["name"]
    rows = await ca._ledger(db, material=material)
    balances = await ca._balances(db, material)
    periods = ca.periods_between(max(period_from, ca.PERIOD_FLOOR), period_to)
    months = ca.roll_months(rows, balances, material, periods, closed)
    history = await _prod_history(db, material)
    pcs = {p["period"]: p for p in trace["product_cost"]}
    ia_rows = [r for r in rows if r.category == "out_adjust" and period_from <= r.period <= period_to]
    landing = await ca._landings(db, ia_rows)
    settlements = ca.settlement_events(rows)

    findings = []
    for mo in months:
        t = mo["period"]
        if t in pcs:
            f = await _cost_finding(db, closed, th, material, name, pcs[t], history)
            if f:
                findings.append(f)
        f = await _carry_finding(db, material, mo, rows)
        if f:
            findings.append(f)
        findings += await _tail_dump_finding(db, th, material, mo, ia_rows, landing,
                                             settlements) or []

    order = {"cost": 0, "carry": 1, "tail": 2}
    findings.sort(key=lambda f: (f["period"], order[f["kind"]]))
    chain, seen = [], set()
    for f in findings:
        for s in f["steps"]:
            k = (s["period"], s["text"])
            if k not in seen:
                seen.add(k)
                chain.append(s)
    chain.sort(key=lambda s: s["period"])
    root_chain: list[dict] = []
    if findings and _depth > 0:
        cost = [f for f in findings if f["kind"] == "cost" and f["related"]]
        if cost:
            f = sorted(cost, key=lambda f: (f["severity"] != "high", f["period"]))[0]
            for r in f["related"]:
                root_chain = await _root(db, r, f["period"], _depth, {material})
                if root_chain:
                    break
            f["root_chain"] = root_chain
    if findings:
        worst = sorted(findings, key=lambda f: (f["severity"] != "high", f["period"]))[0]
        headline = f"{material}: {worst['title']}."
        if root_chain:
            headline += f" Root cause upstream: {root_chain[-1]['headline']}."
    else:
        headline = (f"{material}: nothing abnormal in {period_from} – {period_to} — production "
                    f"cost, month-end value and adjustments are all within the thresholds.")
    return {"material": {"code": material, "name": name}, "headline": headline,
            "chain": chain, "findings": findings, "root_chain": root_chain,
            "related": sorted({r for f in findings for r in f["related"]} - {material})}
