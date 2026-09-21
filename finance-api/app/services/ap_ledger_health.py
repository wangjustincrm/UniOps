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


async def supplier_detail(db: AsyncSession, supplier_code: str, currency: str,
                          limit: int = 500) -> dict:
    """The evidence behind one supplier's gap.

    An aggregate cannot be judged, so this returns the documents: the bills NC
    still flags open, WITH how much has already been paid against each one, and
    the payments themselves with the payable they point at.

    Two things learned the hard way about NC's pointers (2026-09-21):

    * The link from a payment to a payable is `TOP_BILLID` / `TOP_BILLTYPE`
      ('F1' = 应付单). 17,485 of 17,591 payment lines carry it and every one
      resolves. `SRC_BILLID` is the ORIGIN document — type '21' is 采购订单, a
      purchase order — so reading it as "what this payment settled" both prints
      a meaningless code and reports a linked payment as unlinked.
    * Which makes the real finding sharper, not weaker: bill D12021092200128305
      was billed 36,040.00, has 36,040.00 of payments pointing squarely at it,
      and both of its lines are still flagged fully open. The payment is
      attached; the balance was simply never cleared.

    Bills are grouped by document because "paid against" is a property of the
    bill, not of one of its lines — repeating a bill-level total on every line
    would read as if it had been paid several times over.
    """
    lim = max(1, min(limit, 2000))
    bills = (await db.execute(text("""
        select l.bill_no,
               min(l.bill_date)::date as bill_date,
               min(l.bill_year) as bill_year,
               sum(l.money_cr)  as money_cr,
               sum(l.money_bal) as money_bal,
               min(l.invoice_no) as invoice_no,
               min(l.purchase_order) as purchase_order,
               min(b.trade_type) as trade_type,
               -- What NC's own payment documents say was paid against this
               -- exact bill. When this equals the billed amount and the bill is
               -- still open, the payment was made and never applied.
               coalesce((select sum(p.money_de)
                           from nc_ap_payment_lines p
                          where p.top_bill_id = min(b.nc_pk)), 0) as paid_against
          from nc_ap_bill_lines l
          join nc_ap_bills b on b.id = l.bill_id
         where l.supplier_code = :code and b.currency = :ccy and l.money_bal <> 0
         group by l.bill_no
         order by min(l.bill_date), l.bill_no
         limit :lim
    """), {"code": supplier_code, "ccy": currency, "lim": lim})).mappings().all()

    payments = (await db.execute(text("""
        select p.bill_no, p.pay_date::date as pay_date, p.bill_date::date as doc_date,
               p.bill_year, l.money_de, l.top_bill_type, l.top_bill_id,
               ab.bill_no as applied_to_bill_no,
               ab.id is not null as applied_bill_still_open_known,
               coalesce((select sum(x.money_bal) from nc_ap_bill_lines x
                          where x.bill_id = ab.id), 0) as applied_bill_still_open,
               p.scomment
          from nc_ap_payment_lines l
          join nc_ap_payments p on p.id = l.payment_id
          left join nc_ap_bills ab on ab.nc_pk = l.top_bill_id
         where l.supplier_code = :code and l.currency = :ccy
         order by p.bill_date desc nulls last, p.bill_no desc
         limit :lim
    """), {"code": supplier_code, "ccy": currency, "lim": lim})).mappings().all()

    def d(v):
        return v.isoformat() if v else None

    def m(v):
        return str(v) if v is not None else None

    return {
        "supplier_code": supplier_code,
        "currency": currency,
        "open_bills": [{
            "bill_no": r["bill_no"], "bill_date": d(r["bill_date"]),
            "bill_year": r["bill_year"],
            "money_cr": m(r["money_cr"]), "money_bal": m(r["money_bal"]),
            "paid_against": m(r["paid_against"]),
            "invoice_no": r["invoice_no"], "purchase_order": r["purchase_order"],
            "trade_type": r["trade_type"],
        } for r in bills],
        "payments": [{
            "bill_no": r["bill_no"], "pay_date": d(r["pay_date"]),
            "doc_date": d(r["doc_date"]), "bill_year": r["bill_year"],
            "money_de": m(r["money_de"]),
            # The payable this payment points at, by its NUMBER — the code is
            # of no use to anyone reading the page.
            "applied_to_bill_no": r["applied_to_bill_no"],
            "applied_bill_still_open": m(r["applied_bill_still_open"]),
            "scomment": r["scomment"],
        } for r in payments],
    }
