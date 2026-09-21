"""Is NC's payable subledger telling the truth about what is still owed?

Yes — once you only count documents NC actually approved. That correction came
from Justin looking at an NC bill-entry screen, seeing no `money_bal` field on
it, and asking whether the column is used at all. It is; the mistake was mine,
for never filtering on document status.

What the numbers say (NC production, 2026-09-22):

    status        lines    billed            open balance
    approved     17,896    191,116,917.52    10,452,032.99
    unapproved      887     35,483,410.06    35,483,410.06
    other           168        963,404.33       963,404.33

Of 46.9M "open", 36.4M sits on documents NC never approved; the real payable is
~10.45M. And among approved bills the subledger is all but perfect: 162
supplier/currency pairs where open == billed - paid to the cent, against 2 that
differ by 44,667.12 in total.

So the finding is NOT "the subledger does not clear". It is:

  **excluded**      payables raised and never approved, still carrying a
                    balance: 478 bills with no payment at all, 31 whose only
                    payments are THEMSELVES unapproved drafts (checked
                    2026-09-22: zero approved payment lines against any of
                    them), and 100 more in other non-approved states. Nothing
                    real ever happened on any of them, so finance does not need
                    to work them — but the page still states how much was left
                    out, because a silently dropped 33.5M is exactly the kind
                    of thing that turns into "why does this not match NC".
  **inconsistent**  approved bills where open and billed-minus-paid still
                    disagree. Nearly empty today, kept as a tripwire so a real
                    clearing failure cannot appear later unnoticed.

`billed - paid` stays a DISCREPANCY DETECTOR, not a second valuation: paying
past what was billed is a prepayment or an unapplied credit, not an error.

Currency is never mixed — this company transacts in CAD, USD, CNY and EUR.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CONSISTENT = "consistent"
INCONSISTENT = "inconsistent"
ABANDONED = "abandoned"

# A payable NC stands behind. Everything else is a draft, a duplicate or a void.
_EFFECTIVE = "b.bill_status = 1 and b.approve_status = 1"

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
     where {_EFFECTIVE}
     group by l.supplier_code, b.currency
),
paid as (
    select l.supplier_code, l.currency, sum(l.money_de) as paid
      from nc_ap_payment_lines l
      join nc_ap_payments p on p.id = l.payment_id
     where p.bill_status = 1 and p.approve_status = 1
     group by l.supplier_code, l.currency
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
           case when abs(gap) <= {TOLERANCE} then '{CONSISTENT}' else '{INCONSISTENT}' end as health
      from joined j
     where subledger_open <> 0
)
"""


_EMPTY_ABANDONED = {"bills": 0, "money_bal": "0", "superseded_bills": 0, "superseded_bal": "0"}

# Documents NC never approved that still carry a balance. `superseded` marks the
# ones whose supplier and invoice number also appear on an APPROVED bill — an
# abandoned duplicate, the cheapest kind to clear.
_ABANDONED = f"""
with ab as (
    select b.bill_no, b.currency, b.bill_date::date as bill_date, b.bill_year,
           b.bill_status, b.approve_status,
           l.supplier_code, l.supplier_name, l.invoice_no, l.invoice_no_norm,
           l.money_cr, l.money_bal
      from nc_ap_bill_lines l
      join nc_ap_bills b on b.id = l.bill_id
     where not ({_EFFECTIVE}) and l.money_bal <> 0
),
flagged as (
    select ab.*,
           (ab.invoice_no_norm is not null and exists (
              select 1 from nc_ap_bill_lines l2
              join nc_ap_bills b2 on b2.id = l2.bill_id
               where b2.bill_status = 1 and b2.approve_status = 1
                 and l2.invoice_no_norm = ab.invoice_no_norm
                 and l2.supplier_code is not distinct from ab.supplier_code
           )) as superseded
      from ab
)
"""


async def abandoned_summary(db: AsyncSession) -> dict:
    rows = (await db.execute(text(_ABANDONED + """
        select currency,
               count(distinct bill_no) as bills,
               coalesce(sum(money_bal), 0) as money_bal,
               count(distinct bill_no) filter (where superseded) as superseded_bills,
               coalesce(sum(money_bal) filter (where superseded), 0) as superseded_bal
          from flagged group by currency
    """))).mappings().all()
    return {(r["currency"] or "(unknown)"): {
        "bills": int(r["bills"]), "money_bal": str(r["money_bal"]),
        "superseded_bills": int(r["superseded_bills"]),
        "superseded_bal": str(r["superseded_bal"]),
    } for r in rows}


async def abandoned_items(db: AsyncSession, currency: str | None = None,
                          limit: int = 500) -> dict:
    """Payable documents NC never approved, biggest balance first."""
    params: dict = {"limit": max(1, min(limit, 2000))}
    clause = ""
    if currency:
        clause = " where currency = :ccy"
        params["ccy"] = currency
    rows = (await db.execute(text(_ABANDONED + f"""
        select bill_no, currency, bill_date, bill_year, bill_status, approve_status,
               supplier_code, max(supplier_name) as supplier_name,
               max(invoice_no) as invoice_no,
               sum(money_cr) as money_cr, sum(money_bal) as money_bal,
               bool_or(superseded) as superseded
          from flagged {clause}
         group by bill_no, currency, bill_date, bill_year, bill_status,
                  approve_status, supplier_code
         order by abs(sum(money_bal)) desc
         limit :limit
    """), params)).mappings().all()
    return {"total": len(rows), "items": [{
        "bill_no": r["bill_no"], "currency": r["currency"],
        "bill_date": r["bill_date"].isoformat() if r["bill_date"] else None,
        "bill_year": r["bill_year"], "bill_status": r["bill_status"],
        "approve_status": r["approve_status"], "supplier_code": r["supplier_code"],
        "supplier_name": r["supplier_name"], "invoice_no": r["invoice_no"],
        "money_cr": str(r["money_cr"]), "money_bal": str(r["money_bal"]),
        "superseded": r["superseded"],
    } for r in rows]}


async def summary(db: AsyncSession) -> dict:
    """Per currency: the balance that can be used, and the two kinds that cannot."""
    abandoned = await abandoned_summary(db)
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
        entry = by_ccy.setdefault(ccy, {"currency": ccy, CONSISTENT: None, INCONSISTENT: None})
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
                    "inconsistent": e[INCONSISTENT] or empty,
                    "abandoned": abandoned.get(ccy) or _EMPTY_ABANDONED})
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
           and b.bill_status = 1 and b.approve_status = 1
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
           and p.bill_status = 1 and p.approve_status = 1
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
