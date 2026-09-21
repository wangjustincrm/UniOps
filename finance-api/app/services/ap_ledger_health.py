"""Is NC's payable subledger telling the truth about what is still owed?

For most suppliers, yes. For some, badly not — and the difference decides
whether a cash-flow forecast can be built on `money_bal` at all.

The evidence (NC production, 2026-09-21): 2021 milk was billed 32,641,249.87
and paid 32,114,350.99, yet 13,411,519.10 of those lines are still flagged open.
The money moved; the payment was never applied against the lines. Across the
whole population the subledger says 46.9M open while billed-minus-paid says
26.9M — about 20M of payable that exists only as an uncleared flag.

So this check splits suppliers in two:

  consistent  subledger open == billed - paid. Its open balance can be used.
  uncleared   they disagree. Its open balance overstates, by the gap shown.

**What this does NOT claim.** `billed - paid` is not "the true amount owed"
either — payments can include prepayments and credits that were never tied to a
bill, and a supplier can legitimately hold both. The comparison is a
DISCREPANCY DETECTOR, not a second valuation: it says the two figures NC itself
holds do not agree, and how far apart they are. Which one is right is a
question for finance, per supplier.

**Currency is never mixed.** NC stores original-currency amounts and this
company transacts in CAD, USD, CNY and EUR. Summing across them would produce a
number with no meaning, so every row here is per (supplier x currency).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CONSISTENT = "consistent"
UNCLEARED = "uncleared"

# Both sides are 2dp in NC; this guards representation noise, not fuzziness.
TOLERANCE = "0.01"

_BASE = f"""
with billed as (
    select l.supplier_code, b.currency,
           sum(l.money_cr)  as billed,
           sum(l.money_bal) as subledger_open,
           max(l.supplier_name) as supplier_name
      from nc_ap_bill_lines l
      join nc_ap_bills b on b.id = l.bill_id
     group by l.supplier_code, b.currency
),
paid as (
    select supplier_code, currency, sum(money_de) as paid
      from nc_ap_payment_lines
     group by supplier_code, currency
),
joined as (
    select b.supplier_code, b.currency, b.supplier_name,
           b.billed, coalesce(p.paid, 0) as paid, b.subledger_open,
           b.billed - coalesce(p.paid, 0) as billed_minus_paid,
           b.subledger_open - (b.billed - coalesce(p.paid, 0)) as gap
      from billed b
      left join paid p
        on p.supplier_code is not distinct from b.supplier_code
       and p.currency is not distinct from b.currency
),
classified as (
    select j.*,
           case when abs(gap) <= {TOLERANCE} then '{CONSISTENT}' else '{UNCLEARED}' end as health
      from joined j
     where subledger_open <> 0
)
"""


async def summary(db: AsyncSession) -> dict:
    """Per currency, how much of the open balance can be trusted."""
    sql = _BASE + """
    select currency, health,
           count(*) as suppliers,
           coalesce(sum(subledger_open), 0) as subledger_open,
           coalesce(sum(billed_minus_paid), 0) as billed_minus_paid,
           coalesce(sum(gap), 0) as gap
      from classified
     group by currency, health
     order by currency, health
    """
    rows = (await db.execute(text(sql))).mappings().all()
    by_ccy: dict[str, dict] = {}
    for r in rows:
        ccy = r["currency"] or "(unknown)"
        entry = by_ccy.setdefault(ccy, {"currency": ccy, CONSISTENT: None, UNCLEARED: None})
        entry[r["health"]] = {
            "suppliers": int(r["suppliers"]),
            "subledger_open": str(r["subledger_open"]),
            "billed_minus_paid": str(r["billed_minus_paid"]),
            "gap": str(r["gap"]),
        }
    empty = {"suppliers": 0, "subledger_open": "0", "billed_minus_paid": "0", "gap": "0"}
    out = []
    for ccy in sorted(by_ccy):
        e = by_ccy[ccy]
        out.append({"currency": ccy,
                    "consistent": e[CONSISTENT] or empty,
                    "uncleared": e[UNCLEARED] or empty})
    return {"currencies": out}


async def items(db: AsyncSession, health: str | None = None,
                currency: str | None = None, limit: int = 200) -> dict:
    """Supplier-level detail, worst gap first — that is the order it gets
    worked in, and the biggest gaps are the ones distorting the forecast."""
    where, params = [], {"limit": max(1, min(limit, 1000))}
    if health:
        where.append("health = :health")
        params["health"] = health
    if currency:
        where.append("currency = :currency")
        params["currency"] = currency
    clause = (" where " + " and ".join(where)) if where else ""
    sql = _BASE + f"""
    select supplier_code, supplier_name, currency, health,
           billed, paid, subledger_open, billed_minus_paid, gap
      from classified
      {clause}
     order by abs(gap) desc, subledger_open desc
     limit :limit
    """
    rows = (await db.execute(text(sql), params)).mappings().all()
    return {"total": len(rows), "items": [{
        "supplier_code": r["supplier_code"],
        "supplier_name": r["supplier_name"],
        "currency": r["currency"],
        "health": r["health"],
        "billed": str(r["billed"]),
        "paid": str(r["paid"]),
        "subledger_open": str(r["subledger_open"]),
        "billed_minus_paid": str(r["billed_minus_paid"]),
        "gap": str(r["gap"]),
    } for r in rows]}


async def trusted_supplier_codes(db: AsyncSession, currency: str | None = None) -> set[str]:
    """Suppliers whose open balance the cash-flow layers may use directly.

    Kept as a function rather than a stored flag so it can never go stale: it is
    recomputed from the mirror every time, like everything else above it.
    """
    sql = _BASE + " select distinct supplier_code from classified where health = :h"
    params: dict = {"h": CONSISTENT}
    if currency:
        sql += " and currency = :ccy"
        params["ccy"] = currency
    rows = (await db.execute(text(sql), params)).scalars().all()
    return {r for r in rows if r}
