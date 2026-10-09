"""AI Financial Analysis — deterministic cost-chain analysis over the NC mirror.

Spec: docs/superpowers/specs/2026-10-09-ai-financial-analysis-design.md §6–§7.

Everything here is computed from NC's own numbers (nc_ia_ledger, nc_cm_*); no
figure is invented and nothing is written back. Two rules from the spec shape
the code:

  * §6.0 — usage means MATERIAL CONSUMPTION (I6, trade types 4D-*), never
    production picking. Picking (4A-Cxx-001 / 4I-Cxx-005) only moves stock
    from the main warehouse to the line, so it lives with the other warehouse
    transfers and is netted, not counted.
  * Nothing is dropped quietly. A transfer leg without its partner, a ledger
    that does not land on NC's own month-end balance, a settlement whose
    provisional receipt we cannot find — each is reported, never absorbed.

The worked example the module exists for (CR0059 Pasteurized Skim Milk,
July–September 2026) is the acceptance test: provisional milk components at
5.058 consumed in July, settled at 4.2 on 14 August, the -61,906.55 variance
pushed into August's tiny 39,411 kg run (-1.2112/kg), carried into September
and dumped by NC's tail adjustment IA2026092900003299 (-46,588.85) onto CS0080.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.nc_cost_sync import BALANCE_FLOOR, PERIOD_FLOOR

D0 = Decimal("0")
QTY_EPS = Decimal("0.000001")
AMT_EPS = Decimal("0.01")

DEFAULT_THRESHOLDS = {
    # A2: production unit cost moved more than this fraction vs the previous
    # producing period.
    "a2_unit_cost_change": 0.30,
    # A3: a closing balance with zero quantity is only flagged above this
    # amount (NC leaves cent-level tails all the time).
    "a3_min_amount": 1.0,
    # A4: provisional→settlement variance as a share of the material's
    # consumption in the same period, and an absolute floor below which it is
    # noise.
    "a4_variance_ratio": 0.20,
    "a4_min_amount": 1000.0,
    # A6: a single outbound adjustment (IA) at least this large.
    "a6_min_amount": 5000.0,
}

# §6.2 — warehouse-transfer trade types, paired in → out. Codes, not names:
# names are free text an administrator can edit. Legs pair on the UPSTREAM
# document they were both generated from (see 0040's note on upstream_code);
# a form conversion turns one material into another, so its legs pair on the
# document alone and only the amount must agree.
TRANSFER_FAMILIES = {
    "line_picking":   ("4A-Cxx-001", "4I-Cxx-005"),   # 生产领料 — NOT consumption (§6.0)
    "line_return":    ("4A-Cxx-002", "4I-Cxx-006"),   # 生产退料
    "cost_transfer":  ("4A-Cxx-010", "4I-Cxx-015"),   # 库存调整（传成本）
    "fg_transfer":    ("4A-Cxx-093", "4I-Cxx-093"),   # 产成品转库
    "other_business": ("4A-Cxx-099", "4I-Cxx-099"),   # 库存其它业务
    "amazon":         ("4A-Cxx-103", "4I-Cxx-103"),   # FG SC to Amazon
}
_FAMILY_OF = {code: fam for fam, pair in TRANSFER_FAMILIES.items() for code in pair}
CROSS_MATERIAL_FAMILIES = {"cost_transfer"}

# CM material collection source types, measured on the 2026 mirror:
#   12  conversion cost collected at cost-centre level (no cost object) — not
#       part of any one product's cost, never read here
#   18  material consumption (the only type with a quantity)
#   24  conversion cost allocated to the cost object (payroll, overhead,
#       utilities — element only, no material)
#   31  settlement-variance adjustment: a provisional price settled
#       differently after the stock was used
#   60  month-end tail adjustment: NC's IA posted when an issuing material's
#       quantity reached zero with value left (the CR0059 → CS0080 dump)
STUFF_CONSUMED = 18
STUFF_CONVERSION = 24
STUFF_SETTLEMENT = 31
STUFF_TAIL = 60
ADJUSTMENT_TYPES = (STUFF_SETTLEMENT, STUFF_TAIL)

# §6.0 — the consumption trade types seen in 2026. Anything else under I6 is
# still consumption by bill type, but reported as unclassified.
CONSUMPTION_TYPES = {
    "4D-Cxx-001": "Production material consumption",
    "4D-01": "Production consumption",
    "4D-Cxx-CRM02": "Component consumption",
}

CATEGORY_LABELS = {
    "purchase_provisional": "Provisional receipt",
    "provisional_reversal": "Provisional reversal",
    "settlement": "Settlement receipt",
    "purchase": "Purchase receipt",
    "production": "Production receipt",
    "consumption": "Material consumption",
    "cogs": "Cost of sales",
    "transfer_in": "Transfer in",
    "transfer_out": "Transfer out",
    "other_in": "Other receipt",
    "other_out": "Other issue",
    "in_adjust": "Receipt adjustment",
    "out_adjust": "Issue adjustment",
}


# ── small helpers ────────────────────────────────────────────────────────────

def prev_period(p: str) -> str:
    y, m = int(p[:4]), int(p[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def periods_between(a: str, b: str) -> list[str]:
    out, p = [], a
    while p <= b:
        out.append(p)
        y, m = int(p[:4]), int(p[5:7])
        p = f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"
    return out


def _n(v) -> Decimal:
    return Decimal(str(v)) if v is not None else D0


def _f(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def _unit(amount: Decimal, qty: Decimal) -> Decimal | None:
    return (amount / qty).quantize(Decimal("0.0001")) if abs(qty) > QTY_EPS else None


@dataclass
class LedgerRow:
    nc_pk: str
    period: str
    bill_type: str
    bill_code: str | None
    biz_date: object
    direction: int
    material_code: str
    material_name: str | None
    qty: Decimal
    price: Decimal | None
    amount: Decimal
    batch_code: str | None
    mo_code: str | None
    src_type: str | None
    src_code: str | None
    upstream_code: str | None
    trantype_code: str | None
    trantype_name: str | None
    estimate_flag: str | None
    audit_seq: int | None
    category: str = ""
    note: str | None = None


_LEDGER_COLS = ("nc_pk, period, bill_type, bill_code, biz_date, direction, material_code, "
                "material_name, qty, price, amount, batch_code, mo_code, src_type, src_code, "
                "upstream_code, trantype_code, trantype_name, estimate_flag, audit_seq, note")


def _row(r) -> LedgerRow:
    return LedgerRow(
        nc_pk=r[0], period=r[1], bill_type=r[2], bill_code=r[3], biz_date=r[4],
        direction=int(r[5]), material_code=r[6], material_name=r[7], qty=_n(r[8]),
        price=None if r[9] is None else _n(r[9]), amount=_n(r[10]), batch_code=r[11],
        mo_code=r[12], src_type=r[13], src_code=r[14], upstream_code=r[15],
        trantype_code=r[16], trantype_name=r[17], estimate_flag=r[18], audit_seq=r[19],
        note=r[20])


def classify(rows: list[LedgerRow]) -> None:
    """Assign each ledger row its category, in place.

    Purchase receipts (I2) need the whole set at once: a settlement receipt is
    only a *settlement* if a provisional reversal sits beside it under the same
    settlement bill and batch — otherwise it is an ordinary purchase invoiced
    the month it arrived, and treating it as a variance would report the whole
    purchase as one.
    """
    reversal_keys = {(r.material_code, r.src_code, r.batch_code) for r in rows
                     if r.bill_type == "I2" and r.estimate_flag == "Y" and r.src_type == "27"}
    for r in rows:
        bt = r.bill_type
        if bt == "I2":
            if r.estimate_flag == "Y" and r.src_type == "27":
                r.category = "provisional_reversal"
            elif r.estimate_flag == "Y":
                r.category = "purchase_provisional"
            elif (r.material_code, r.src_code, r.batch_code) in reversal_keys:
                r.category = "settlement"
            else:
                r.category = "purchase"
        elif bt == "I3":
            r.category = "production"
        elif bt == "I6":
            r.category = "consumption"
        elif bt == "I5":
            r.category = "cogs"
        elif bt in ("I4", "I7") and r.trantype_code in _FAMILY_OF:
            r.category = "transfer_in" if r.direction == 0 else "transfer_out"
        elif bt == "I9":
            r.category = "in_adjust"
        elif bt == "IA":
            r.category = "out_adjust"
        else:
            r.category = "other_in" if r.direction == 0 else "other_out"


async def _ledger(db: AsyncSession, *, material: str | None = None) -> list[LedgerRow]:
    sql = f"select {_LEDGER_COLS} from nc_ia_ledger where dr = 0"
    params = {}
    if material:
        sql += " and material_code = :m"
        params["m"] = material
    sql += " order by period, audit_seq nulls last, nc_pk"
    rows = [_row(r) for r in (await db.execute(text(sql), params)).all()]
    classify(rows)
    return rows


async def _balances(db: AsyncSession, material: str | None = None) -> dict:
    """NC's own month-end balances: (material, period) -> (qty, amount)."""
    sql = "select material_code, period, qty, amount from nc_ia_month_balances where dr = 0"
    params = {}
    if material:
        sql += " and material_code = :m"
        params["m"] = material
    out = {}
    for m, p, q, a in (await db.execute(text(sql), params)).all():
        k = (m, p)
        q0, a0 = out.get(k, (D0, D0))
        out[k] = (q0 + _n(q), a0 + _n(a))
    return out


async def closed_periods(db: AsyncSession) -> set[str]:
    """Periods NC has closed. IA_MONTHNAB is written by NC's month-end close
    and by nothing else — measured 2026-10-09: rows through 2026-08, none for
    September although its cost calculation had already run (its tail
    adjustments exist). A period with no balance rows at all is therefore
    open, its figures can still move, and there is no NC balance to hold the
    ledger roll against."""
    res = await db.execute(text(
        "select distinct period from nc_ia_month_balances where dr = 0"))
    return {r[0] for r in res.all()}


async def load_thresholds(db: AsyncSession) -> dict:
    raw = (await db.execute(text("select thresholds from fa_config where id = 1"))).scalar()
    th = dict(DEFAULT_THRESHOLDS)
    for k, v in (raw or {}).items():
        if k in th and isinstance(v, (int, float)) and not isinstance(v, bool):
            th[k] = float(v)
    return th


# ── monthly roll ─────────────────────────────────────────────────────────────

def roll_months(rows: list[LedgerRow], balances: dict, material: str,
                periods: list[str], closed: set[str]) -> list[dict]:
    """§6.1 — opening + receipts − issues = closing, month by month, from the
    start of the mirror so the first displayed month has a true opening."""
    by_period = defaultdict(list)
    for r in rows:
        by_period[r.period].append(r)
    q, a = balances.get((material, BALANCE_FLOOR), (D0, D0))
    out = []
    for p in periods_between(PERIOD_FLOOR, periods[-1]) if periods else []:
        flows = defaultdict(lambda: [D0, D0])
        in_q = in_a = out_q = out_a = D0
        for r in by_period.get(p, []):
            f = flows[r.category]
            f[0] += r.qty
            f[1] += r.amount
            if r.direction == 0:
                in_q += r.qty
                in_a += r.amount
            else:
                out_q += r.qty
                out_a += r.amount
        open_q, open_a = q, a
        q, a = q + in_q - out_q, a + in_a - out_a
        nc = balances.get((material, p))
        nc_q, nc_a = nc if nc else (D0, D0)
        is_closed = p in closed
        prod_q, prod_a = flows["production"] if "production" in flows else (D0, D0)
        out.append({
            "period": p,
            "opening": {"qty": _f(open_q), "amount": _f(open_a)},
            "flows": {k: {"qty": _f(v[0]), "amount": _f(v[1]),
                          "label": CATEGORY_LABELS.get(k, k)} for k, v in flows.items()},
            "receipts": {"qty": _f(in_q), "amount": _f(in_a)},
            "issues": {"qty": _f(out_q), "amount": _f(out_a)},
            "closing": {"qty": _f(q), "amount": _f(a), "unit": _f(_unit(a, q))},
            "nc_balance": {"qty": _f(nc_q), "amount": _f(nc_a)} if nc else None,
            "period_closed": is_closed,
            # Only a closed period has an NC balance to hold the roll against;
            # an open one is None ("not checked"), never True.
            "balance_matches": ((abs(q - nc_q) <= QTY_EPS and abs(a - nc_a) <= AMT_EPS)
                                if is_closed else None),
            "production_unit_cost": _f(_unit(prod_a, prod_q)) if prod_q > QTY_EPS else None,
            "_q": q, "_a": a, "_prod_q": prod_q, "_prod_a": prod_a,
        })
    wanted = set(periods)
    return [m for m in out if m["period"] in wanted]


def settlement_events(rows: list[LedgerRow]) -> list[dict]:
    """Provisional receipt → reversal + settlement, one event per settlement
    bill × batch. Variance = settlement + reversal (the reversal is negative)."""
    prov = defaultdict(list)
    for r in rows:
        if r.category == "purchase_provisional":
            prov[(r.material_code, r.batch_code)].append(r)
    groups = defaultdict(lambda: {"rev": [], "set": []})
    for r in rows:
        if r.category == "provisional_reversal":
            groups[(r.material_code, r.src_code, r.batch_code)]["rev"].append(r)
        elif r.category == "settlement":
            groups[(r.material_code, r.src_code, r.batch_code)]["set"].append(r)
    out = []
    for (mat, js, batch), g in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        rev_q = sum((r.qty for r in g["rev"]), D0)
        rev_a = sum((r.amount for r in g["rev"]), D0)
        set_q = sum((r.qty for r in g["set"]), D0)
        set_a = sum((r.amount for r in g["set"]), D0)
        p = [x for x in prov.get((mat, batch), []) if abs(x.qty + rev_q) <= QTY_EPS]
        period = (g["set"] or g["rev"])[0].period
        out.append({
            "material_code": mat, "batch_code": batch, "settlement_code": js,
            "settlement_period": period,
            "provisional": ({"bill_code": p[0].src_code, "receipt_code": p[0].bill_code,
                             "period": p[0].period, "qty": _f(p[0].qty),
                             "unit": _f(_unit(p[0].amount, p[0].qty)),
                             "amount": _f(p[0].amount)} if p else None),
            "reversal": {"qty": _f(rev_q), "amount": _f(rev_a)},
            "settled": {"qty": _f(set_q), "amount": _f(set_a),
                        "unit": _f(_unit(set_a, set_q))},
            "variance": _f(set_a + rev_a),
            "_variance": set_a + rev_a,
        })
    return out


def transfer_breaks(rows: list[LedgerRow]) -> list[dict]:
    """§6.2 — pair transfer legs on the upstream document both were generated
    from (ZK transfer order / XT form conversion) and, except for a form
    conversion, the material. A group whose legs disagree is a break; a leg
    with no upstream document at all is a break on its own. Netting by month
    instead would hide CR0177's Feb→Mar pair."""
    groups = defaultdict(lambda: {"in": [], "out": []})
    for r in rows:
        if r.category in ("transfer_in", "transfer_out"):
            fam = _FAMILY_OF[r.trantype_code]
            if not r.upstream_code:
                key = (fam, None, r.material_code, r.nc_pk)     # unpaired by construction
            elif fam in CROSS_MATERIAL_FAMILIES:
                key = (fam, r.upstream_code, None, None)
            else:
                key = (fam, r.upstream_code, r.material_code, None)
            groups[key]["in" if r.category == "transfer_in" else "out"].append(r)
    out = []
    for (fam, upstream, mat, _), g in groups.items():
        iq = sum((r.qty for r in g["in"]), D0)
        ia = sum((r.amount for r in g["in"]), D0)
        oq = sum((r.qty for r in g["out"]), D0)
        oa = sum((r.amount for r in g["out"]), D0)
        qty_ok = fam in CROSS_MATERIAL_FAMILIES or abs(iq - oq) <= QTY_EPS
        if upstream and qty_ok and abs(ia - oa) <= AMT_EPS:
            continue
        legs = g["in"] + g["out"]
        mat = mat or legs[0].material_code
        out.append({
            "family": fam, "upstream_code": upstream,
            "src_codes": sorted({r.src_code for r in legs if r.src_code}),
            "materials": sorted({r.material_code for r in legs if r.material_code}),
            "material_code": mat,
            "material_name": legs[0].material_name,
            "periods": sorted({r.period for r in legs}),
            "in": {"qty": _f(iq), "amount": _f(ia),
                   "bills": sorted({r.bill_code for r in g["in"] if r.bill_code})},
            "out": {"qty": _f(oq), "amount": _f(oa),
                    "bills": sorted({r.bill_code for r in g["out"] if r.bill_code})},
            "difference": {"qty": _f(iq - oq), "amount": _f(ia - oa)},
            "reason": ("no upstream transfer document in NC" if not upstream
                       else "legs disagree"),
        })
    return sorted(out, key=lambda x: (x["periods"][0], x["material_code"] or ""))


async def _mo_products(db: AsyncSession, mo_codes: set[str]) -> dict:
    """Production order -> the material(s) its completion receipts brought in."""
    if not mo_codes:
        return {}
    res = await db.execute(text(
        "select distinct mo_code, material_code, material_name from nc_ia_ledger "
        "where dr = 0 and bill_type = 'I3' and mo_code = any(:m)"), {"m": list(mo_codes)})
    out = defaultdict(list)
    for mo, code, name in res.all():
        out[mo].append({"material_code": code, "material_name": name})
    return out


async def _landings(db: AsyncSession, ia_rows: list[LedgerRow]) -> dict:
    """Where each issue adjustment landed, keyed by ledger nc_pk.

    With a production order: the material that order completed. Without one
    (milk-component consumption carries none — spec §6.0): the cost object
    whose CM collection received an amount-only adjustment (source 31) of the
    same material, period and amount."""
    mo_map = await _mo_products(db, {r.mo_code for r in ia_rows if r.mo_code})
    out = {}
    for r in ia_rows:
        if r.mo_code and mo_map.get(r.mo_code):
            out[r.nc_pk] = mo_map[r.mo_code]
            continue
        res = await db.execute(text(
            "select distinct cost_object_code, cost_object_name from nc_cm_stuff_lines "
            "where dr = 0 and source_type = any(:st) and material_code = :m and period = :p "
            "and abs(amount - :a) < 0.01"),
            {"st": list(ADJUSTMENT_TYPES), "m": r.material_code, "p": r.period, "a": r.amount})
        out[r.nc_pk] = [{"material_code": c, "material_name": n, "via": "cost_collection"}
                        for c, n in res.all()]
    return out


VOUCHER_STATUS_LABELS = {
    "posted": "Posted", "unposted": "Unposted", "discarded": "Discarded",
    "tempsave": "Temp-saved", "error": "Error", "deleted": "Deleted in NC",
}


async def vouchers_for(db: AsyncSession, bill_codes: set[str]) -> dict[str, list[dict]]:
    """bill code → the GL vouchers it generated, EVERY status included.

    User, 2026-10-09: the vouchers taking part in an adjustment must include
    unposted ones — September's cost vouchers were all unposted, the tail dump
    onto CS0080 among them."""
    codes = sorted(c for c in bill_codes if c)
    if not codes:
        return {}
    res = await db.execute(text(
        "select bill_code, voucher_no, voucher_period, voucher_date, explanation, "
        "voucher_status, coalesce(sum(src_amount), 0) from nc_cost_vouchers "
        "where dr = 0 and bill_code = any(:c) "
        "group by bill_code, voucher_no, voucher_period, voucher_date, explanation, "
        "voucher_status order by voucher_period, voucher_no"), {"c": codes})
    out: dict[str, list[dict]] = defaultdict(list)
    for code, vno, per, vdate, expl, st, amt in res.all():
        out[code].append({"voucher_no": vno, "period": per,
                          "date": vdate.isoformat() if vdate else None,
                          "explanation": expl, "status": st,
                          "status_label": VOUCHER_STATUS_LABELS.get(st, st),
                          "amount_on_voucher": _f(_n(amt))})
    return out


# ── material trace (§8 Material Trace) ───────────────────────────────────────

async def material_trace(db: AsyncSession, material: str, period_from: str,
                         period_to: str) -> dict:
    rows = await _ledger(db, material=material)
    balances = await _balances(db, material)
    closed = await closed_periods(db)
    periods = periods_between(max(period_from, PERIOD_FLOOR), period_to)
    months = roll_months(rows, balances, material, periods, closed)
    for m in months:
        for k in ("_q", "_a", "_prod_q", "_prod_a"):
            m.pop(k, None)

    in_range = [r for r in rows if period_from <= r.period <= period_to]
    name = next((r.material_name for r in rows if r.material_name), None)

    events = [e for e in settlement_events(rows)
              if period_from <= e["settlement_period"] <= period_to
              or (e["provisional"] and period_from <= e["provisional"]["period"] <= period_to)]
    for e in events:
        e.pop("_variance", None)

    # Issue adjustments, with where they landed.
    ia = [r for r in in_range if r.category == "out_adjust"]
    landing = await _landings(db, ia)
    out_adjustments = [{
        "period": r.period, "bill_code": r.bill_code, "amount": _f(r.amount),
        "attached_to": r.src_code, "mo_code": r.mo_code, "batch_code": r.batch_code,
        "landed_on": landing.get(r.nc_pk, []),
    } for r in ia]

    # Who consumed it (CM material collection, by cost object).
    res = await db.execute(text(
        "select period, cost_object_code, cost_object_name, source_type, "
        "coalesce(sum(qty),0), coalesce(sum(amount),0) from nc_cm_stuff_lines "
        "where dr = 0 and material_code = :m and period between :a and :b "
        "and source_type in (18, 31, 60) "
        "group by period, cost_object_code, cost_object_name, source_type "
        "order by period, cost_object_code, source_type"),
        {"m": material, "a": period_from, "b": period_to})
    consumers = [{
        "period": p, "cost_object_code": c, "cost_object_name": n,
        "kind": {STUFF_SETTLEMENT: "settlement adjustment", STUFF_TAIL: "tail adjustment"}
                .get(st, "consumption"),
        "qty": _f(_n(q)), "amount": _f(_n(a)),
        "unit": _f(_unit(_n(a), _n(q))) if st == STUFF_CONSUMED else None,
    } for p, c, n, st, q, a in res.all()]

    product_cost = await _product_cost(db, material, period_from, period_to)

    # Vouchers behind every adjustment, and behind the issue each one sits on —
    # posted or not. A document with no voucher yet is listed too.
    adj = [r for r in in_range if r.category in ("out_adjust", "in_adjust")]
    attached = {r.src_code for r in adj if r.src_code}
    docs = [(r.bill_code, CATEGORY_LABELS[r.category], r.amount, r.period) for r in adj]
    docs += [(r.bill_code, "Issue an adjustment sits on", r.amount, r.period)
             for r in in_range if r.bill_code in attached]
    vmap = await vouchers_for(db, {d[0] for d in docs})
    seen = set()
    vouchers = []
    for code, kind, amt, per in docs:
        if code in seen:
            continue
        seen.add(code)
        vs = vmap.get(code) or [None]
        for v in vs:
            vouchers.append({"bill_code": code, "document": kind, "period": per,
                             "document_amount": _f(amt), "voucher": v})

    return {
        "material": {"code": material, "name": name},
        "range": {"from": period_from, "to": period_to},
        "months": months,
        "settlements": events,
        "out_adjustments": out_adjustments,
        "consumers": consumers,
        "product_cost": product_cost,
        "vouchers": vouchers,
        "vouchers_unposted": sum(1 for v in vouchers if v["voucher"]
                                 and v["voucher"]["status"] == "unposted"),
        "lines": [{
            "period": r.period, "bill_type": r.bill_type, "bill_code": r.bill_code,
            "biz_date": r.biz_date.isoformat() if r.biz_date else None,
            "category": r.category, "category_label": CATEGORY_LABELS.get(r.category),
            "direction": "in" if r.direction == 0 else "out",
            "trantype": r.trantype_name, "qty": _f(r.qty) if r.qty else None,
            "price": _f(r.price), "amount": _f(r.amount), "batch_code": r.batch_code,
            "mo_code": r.mo_code, "src_code": r.src_code,
            "estimate": r.estimate_flag == "Y",
        } for r in in_range],
    }


async def _product_cost(db: AsyncSession, material: str, period_from: str,
                        period_to: str) -> list[dict]:
    """§6.3 — NC's product cost for this material as a cost object, every
    input split by where its value came from in NC's own collection: consumed
    (18), conversion cost allocated (24), settlement adjustment (31), tail
    adjustment (60) — and carried in from an earlier month, which is whatever
    of NC's figure the month's collection does not explain (e.g. June's
    no-output collection rolled into July)."""
    heads = (await db.execute(text(
        "select nc_pk, period, cost_object_pk, qty, amount from nc_cm_prod_costs "
        "where dr = 0 and material_code = :m and period between :a and :b order by period"),
        {"m": material, "a": period_from, "b": period_to})).all()
    out = []
    for pk, period, obj_pk, qty, amount in heads:
        lines = (await db.execute(text(
            "select material_code, max(material_name), element_code, max(element_name), "
            "coalesce(sum(qty),0), coalesce(sum(amount),0) from nc_cm_prod_cost_lines "
            "where dr = 0 and prod_cost_pk = :pk group by material_code, element_code"),
            {"pk": pk})).all()
        # key: material for material inputs, element for conversion cost
        stuff = defaultdict(lambda: {18: [D0, D0], 24: D0, 31: D0, 60: D0})
        for mat, el, st, q, a in (await db.execute(text(
                "select material_code, element_code, source_type, coalesce(sum(qty),0), "
                "coalesce(sum(amount),0) from nc_cm_stuff_lines where dr = 0 "
                "and cost_object_pk = :o and period = :p "
                "group by material_code, element_code, source_type"),
                {"o": obj_pk, "p": period})).all():
            s = stuff[("m", mat) if mat else ("e", el)]
            if st == STUFF_CONSUMED:
                s[18][0] += _n(q)
                s[18][1] += _n(a)
            elif st in (STUFF_CONVERSION, STUFF_SETTLEMENT, STUFF_TAIL):
                s[st] += _n(a)
        inputs = []
        for mat, name, el_code, el_name, q, a in lines:
            s = stuff.get(("m", mat) if mat else ("e", el_code),
                          {18: [D0, D0], 24: D0, 31: D0, 60: D0})
            nc_amt = _n(a)
            explained = s[18][1] + s[24] + s[31] + s[60]
            inputs.append({
                "material_code": mat, "material_name": name,
                "element_code": el_code, "element_name": el_name,
                "kind": "material" if mat else "conversion",
                "nc_qty": _f(_n(q)), "nc_amount": _f(nc_amt),
                "consumed_qty": _f(s[18][0]), "consumed_amount": _f(s[18][1]),
                "consumed_unit": _f(_unit(s[18][1], s[18][0])),
                "conversion": _f(s[24]),
                "settlement_adj": _f(s[31]), "tail_adj": _f(s[60]),
                "adjustment": _f(s[31] + s[60]),
                "carried_in": _f(nc_amt - explained),
            })
        q, a = _n(qty), _n(amount)
        tot = lambda k: sum((i[k] or 0 for i in inputs), 0.0)  # noqa: E731
        out.append({
            "period": period, "qty": _f(q), "amount": _f(a), "unit": _f(_unit(a, q)),
            "consumed_total": tot("consumed_amount"), "conversion_total": tot("conversion"),
            "settlement_total": tot("settlement_adj"), "tail_total": tot("tail_adj"),
            "carried_total": tot("carried_in"),
            "adjustment_total": tot("adjustment"),
            "inputs": sorted(inputs, key=lambda i: (-(abs(i["nc_amount"] or 0)))),
        })
    return out


def next_period(p: str) -> str:
    y, m = int(p[:4]), int(p[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


async def _flows_next_month(db: AsyncSession, rows: list[LedgerRow], material: str,
                            period: str) -> list[dict]:
    """Where stock left at the end of `period` went in the following month.

    Production crosses month-ends (semi-finished product made at the end of
    September becomes finished goods in early October — user, 2026-10-09), so
    an abnormal value on hand at month-end is consumed by next month's orders.
    Read from consumption (I6) by production order, not from CM collection:
    the next month is usually still open and NC's cost calculation for it has
    not run yet."""
    nxt = next_period(period)
    by_mo = defaultdict(lambda: [D0, D0])
    for r in rows:
        if r.material_code == material and r.period == nxt and r.category == "consumption":
            v = by_mo[r.mo_code]
            v[0] += r.qty
            v[1] += r.amount
    products = await _mo_products(db, {m for m in by_mo if m})
    return [{"period": nxt, "mo_code": mo, "qty": _f(q), "amount": _f(a),
             "unit": _f(_unit(a, q)), "products": products.get(mo, [])}
            for mo, (q, a) in sorted(by_mo.items(), key=lambda kv: kv[0] or "")]


# ── anomalies (§7) ───────────────────────────────────────────────────────────

RULES = {
    "A1": ("high", "Non-positive production unit cost"),
    "A2": ("medium", "Production unit cost swing"),
    "A3": ("high", "Abnormal closing balance"),
    "A4": ("medium", "Large provisional-to-settlement variance"),
    "A5": ("high", "Unpaired warehouse transfer"),
    "A6": ("medium", "Large issue adjustment"),
    "A8": ("high", "Mirror does not agree with NC"),
}


def _anomaly(rule, period, material_code, material_name, metrics, refs=(), detail=None):
    sev, title = RULES[rule]
    return {"rule": rule, "severity": sev, "title": title, "period": period,
            "material_code": material_code, "material_name": material_name,
            "metrics": metrics, "refs": [r for r in refs if r], "detail": detail}


async def anomalies(db: AsyncSession, period_from: str, period_to: str) -> dict:
    th = await load_thresholds(db)
    rows = await _ledger(db)
    balances = await _balances(db)
    closed = await closed_periods(db)
    by_mat = defaultdict(list)
    for r in rows:
        by_mat[r.material_code].append(r)
    periods = periods_between(max(period_from, PERIOD_FLOOR), period_to)
    out: list[dict] = []

    for mat, mrows in by_mat.items():
        name = next((r.material_name for r in mrows if r.material_name), None)
        months = roll_months(mrows, balances, mat, periods_between(PERIOD_FLOOR, period_to),
                             closed)
        prev_unit = None
        prev_period_with_prod = None
        for m in months:
            p = m["period"]
            unit = _unit(m["_prod_a"], m["_prod_q"]) if m["_prod_q"] > QTY_EPS else None
            show = period_from <= p <= period_to
            if show and unit is not None and unit <= 0:
                out.append(_anomaly("A1", p, mat, name, {
                    "qty": _f(m["_prod_q"]), "amount": _f(m["_prod_a"]), "unit": _f(unit)}))
            if (show and unit is not None and prev_unit not in (None, D0)
                    and abs((unit - prev_unit) / abs(prev_unit)) > Decimal(str(th["a2_unit_cost_change"]))):
                out.append(_anomaly("A2", p, mat, name, {
                    "unit": _f(unit), "previous_unit": _f(prev_unit),
                    "previous_period": prev_period_with_prod,
                    "change": _f(((unit - prev_unit) / abs(prev_unit)).quantize(Decimal("0.0001")))}))
            if unit is not None:
                prev_unit, prev_period_with_prod = unit, p
            q, a = m["_q"], m["_a"]
            # Zero quantity with value left is only abnormal once NC has run its
            # month-end cost calculation: before that, issues in the month are
            # still unpriced (amount 0) while receipts are, so every open month
            # shows this for every material that moved — seen in October 2026
            # on the dev mirror. Negative value on hand is reported regardless.
            zero_qty_tail = (abs(q) <= QTY_EPS and abs(a) > Decimal(str(th["a3_min_amount"]))
                             and m["period_closed"])
            if show and (zero_qty_tail or (q > QTY_EPS and a < -AMT_EPS)):
                out.append(_anomaly("A3", p, mat, name, {
                    "closing_qty": _f(q), "closing_amount": _f(a), "unit": _f(_unit(a, q))},
                    detail=("zero quantity, non-zero value" if abs(q) <= QTY_EPS
                            else "negative value on hand")))
            if show and m["balance_matches"] is False:
                out.append(_anomaly("A8", p, mat, name, {
                    "computed": m["closing"], "nc_balance": m["nc_balance"]},
                    detail="ledger roll does not land on NC's month-end balance"))

        consumption = defaultdict(lambda: D0)
        for r in mrows:
            if r.category == "consumption":
                consumption[r.period] += r.amount
        variance = defaultdict(lambda: [D0, []])
        for e in settlement_events(mrows):
            v = variance[e["settlement_period"]]
            v[0] += e["_variance"]
            v[1].append(e)
        for p, (var, evs) in variance.items():
            if not (period_from <= p <= period_to):
                continue
            if abs(var) < Decimal(str(th["a4_min_amount"])):
                continue
            used = consumption.get(p, D0)
            ratio = abs(var) / abs(used) if abs(used) > AMT_EPS else None
            if ratio is None or ratio > Decimal(str(th["a4_variance_ratio"])):
                out.append(_anomaly("A4", p, mat, name, {
                    "variance": _f(var), "consumption": _f(used),
                    "ratio": _f(ratio.quantize(Decimal("0.0001"))) if ratio is not None else None},
                    refs=[e["settlement_code"] for e in evs]
                         + [e["provisional"]["bill_code"] for e in evs if e["provisional"]]))

    for b in transfer_breaks(rows):
        if any(period_from <= p <= period_to for p in b["periods"]):
            out.append(_anomaly("A5", b["periods"][0], b["material_code"], b["material_name"], {
                "family": b["family"], "upstream_code": b["upstream_code"],
                "materials": b["materials"], "in": b["in"], "out": b["out"],
                "difference": b["difference"], "periods": b["periods"]},
                refs=[b["upstream_code"], *b["src_codes"], *b["in"]["bills"],
                      *b["out"]["bills"]], detail=b["reason"]))

    big_ia = [r for r in rows if r.category == "out_adjust"
              and period_from <= r.period <= period_to
              and abs(r.amount) >= Decimal(str(th["a6_min_amount"]))]
    landing = await _landings(db, big_ia)
    for r in big_ia:
        out.append(_anomaly("A6", r.period, r.material_code, r.material_name, {
            "amount": _f(r.amount), "attached_to": r.src_code, "mo_code": r.mo_code,
            "landed_on": landing.get(r.nc_pk, [])}, refs=[r.bill_code, r.src_code]))

    last = (await db.execute(text(
        "select tie_out_ok, tie_out, finished_at from nc_cost_sync_runs "
        "where status = 'success' order by started_at desc limit 1"))).first()
    if last is not None and last[0] is False:
        out.append(_anomaly("A8", None, None, None, {"diffs": (last[1] or {}).get("diffs")},
                            detail="last sync did not tie out against NC"))

    for a in out:
        a["period_closed"] = (a["period"] in closed) if a["period"] else None
        if a["rule"] in ("A1", "A3") and a["material_code"]:
            a["next_month"] = await _flows_next_month(db, by_mat[a["material_code"]],
                                                      a["material_code"], a["period"])
    sev_rank = {"high": 0, "medium": 1}
    out.sort(key=lambda x: (sev_rank[x["severity"]], x["period"] or "", x["rule"],
                            x["material_code"] or ""))
    counts = defaultdict(int)
    for a in out:
        counts[a["rule"]] += 1
    return {"range": {"from": period_from, "to": period_to}, "thresholds": th,
            "open_periods": [p for p in periods if p not in closed],
            "rules": {k: {"severity": v[0], "title": v[1]} for k, v in RULES.items()},
            "counts": dict(counts), "anomalies": out}


async def materials(db: AsyncSession, q: str | None, limit: int = 50) -> list[dict]:
    """Materials with ledger activity in the mirror, for the picker."""
    sql = ("select material_code, max(material_name), count(*) from nc_ia_ledger where dr = 0")
    params: dict = {"lim": limit}
    if q:
        sql += " and (material_code ilike :q or material_name ilike :q)"
        params["q"] = f"%{q}%"
    sql += " group by material_code order by material_code limit :lim"
    return [{"code": c, "name": n, "rows": int(k)}
            for c, n, k in (await db.execute(text(sql), params)).all()]
