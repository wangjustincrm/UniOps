"""What we actually have to pay, and when.

The question finance has been asking since before any of this existed: which
payables are due, and how much cash does the next quarter need. It is built on
NC's approved open balance — the same figure AP Subledger Health calls the real
payable — because NC is the book of record for balances (spec §10.1).

Three things measured on the mirror on 2026-09-23 decide the shape of this
page, and each of them would make a naive forecast wrong:

  **38% of the open balance has no payment term at all.** 3,954,435.98 across
  39 NC suppliers that have no vendor record in UniOps, so there is nothing to
  read a term from. Those are NOT defaulted to net30 and folded into a bucket —
  they are reported as undated, with the amount and the supplier list, because
  a forecast that silently invents a due date for two fifths of the money is
  worse than one that admits the hole.

  **Most of the balance is already long overdue.** Of 9,417,970.42 CAD open,
  7,775,825.81 is on bills dated more than 180 days ago. Run through net30 that
  is a forecast announcing 7.8M "due now", which is neither true nor
  actionable — much of it is the stale tail the dismissal tool exists for. So
  overdue is aged into bands of its own and kept out of the forward buckets,
  and bills finance has set aside are excluded outright.

  **NC's own payment term is unusable.** 370 of 544 open lines carry a
  `pay_term_pk`, across just 2 distinct values, but NCSC has no BD_PAYTERM
  table to resolve them against (checked when the mirror was built). So the
  term comes from our vendor master, where it is editable per supplier.

Currency is never mixed. Everything is derived on read — there is no forecast
table to go stale.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.nc_ap_bill_invoices import invoices, lines_agg

# What epms-api / mdm-api accept for vendors.payment_terms: `net<N>` for any
# N in 1..365 (Net 14, Net 21… not just the form's presets), plus two non-day
# terms. Anything else that reaches the column is treated as no term rather
# than guessed at — the guess would be invisible and would move real money.
NET_TERM_RE = r"^net[0-9]{1,3}$"
MAX_NET_DAYS = 365
NON_DAY_TERMS: dict[str, int] = {
    # Due on delivery and prepaid both mean "nothing to wait for".
    "cod": 0, "prepayment": 0,
}

UNDATED = "undated"

# Ordered ladder — the first predicate that holds wins, so each entry only
# needs its own upper bound. Overdue is aged into bands rather than collapsed,
# because "overdue" covering both last week and 2021 tells finance nothing.
# `{d}` is days from today to the due date; negative is overdue.
BUCKETS: tuple[tuple[str, str, str], ...] = (
    ("overdue_180", "Overdue over 180 days", "{d} < -180"),
    ("overdue_90", "Overdue 91-180 days", "{d} < -90"),
    ("overdue_30", "Overdue 31-90 days", "{d} < -30"),
    ("overdue_now", "Overdue up to 30 days", "{d} < 0"),
    ("due_7", "Due within 7 days", "{d} <= 7"),
    ("due_30", "Due in 8-30 days", "{d} <= 30"),
    ("due_60", "Due in 31-60 days", "{d} <= 60"),
    ("due_90", "Due in 61-90 days", "{d} <= 90"),
    ("due_later", "Due beyond 90 days", ""),        # catch-all, no predicate
)
OVERDUE_KEYS = tuple(k for k, _, _ in BUCKETS if k.startswith("overdue"))
BUCKET_KEYS = tuple(k for k, _, _ in BUCKETS) + (UNDATED,)
BUCKET_LABELS = dict((k, lbl) for k, lbl, _ in BUCKETS) | {
    UNDATED: "No payment term on file",
}

# A term we do not recognise lands in NULL and therefore in `undated`, which
# is the intended behaviour. The cast is nested under the regex branch (not
# AND-ed with it — SQL does not promise left-to-right evaluation), so it only
# ever sees 1-3 digits.
_NET_DAYS = "substr(v.payment_terms, 4)::int"
_TERM_CASE = (
    f"case when v.payment_terms ~ '{NET_TERM_RE}' then "
    f"case when {_NET_DAYS} between 1 and {MAX_NET_DAYS} then {_NET_DAYS} end "
    + " ".join(f"when v.payment_terms = '{k}' then {n}" for k, n in NON_DAY_TERMS.items())
    + " end")

_DAYS_SQL = "(t.due_date - current_date)"
_BUCKET_CASE = ("case when t.due_date is null then '" + UNDATED + "' " + " ".join(
    f"when {pred.format(d=_DAYS_SQL)} then '{key}'"
    for key, _, pred in BUCKETS if pred) + f" else '{BUCKETS[-1][0]}' end")

_BASE = f"""
with dismissed as (
    select bill_no from nc_ap_bill_dismissals where restored_at is null
),
open_bills as (
    -- One row per NC document. `money_bal` is the open amount per LINE, so it
    -- sums; everything else on a payable is a header property and is taken
    -- with min() rather than repeated per line.
    select l.bill_no, b.currency,
           min(l.bill_date)::date as bill_date,
           min(l.supplier_code) as supplier_code,
           min(l.supplier_name) as supplier_name,
           -- Sort key only: a bill carries one invoice per line, often
           -- several. What the page shows is `invoice_lines`.
           min(l.invoice_no) as invoice_no,
           min(l.purchase_order) as purchase_order,
           {lines_agg()} as invoice_lines,
           min(b.trade_type) as trade_type,
           sum(l.money_bal) as open_bal
      from nc_ap_bill_lines l
      join nc_ap_bills b on b.id = l.bill_id
     where b.bill_status = 1 and b.approve_status = 1
       and l.money_bal <> 0
       -- Bills finance has judged not to be real debt never reach the
       -- forecast. That is the whole point of having judged them.
       and not exists (select 1 from dismissed x where x.bill_no = l.bill_no)
     group by l.bill_no, b.currency
),
termed as (
    select o.*,
           v.payment_terms,
           {_TERM_CASE} as term_days,
           case when o.bill_date is not null and {_TERM_CASE} is not null
                then o.bill_date + ({_TERM_CASE}) end as due_date,
           v.id is null as no_vendor_record
      from open_bills o
      -- erp_id is not unique in the vendor master (the same NC supplier has
      -- been created twice more than once), and a plain join repeats every
      -- bill once per copy — inflating the totals, not just the list. Take
      -- one record per supplier: an active one first, then the latest edited.
      left join lateral (
          select vv.id, vv.payment_terms
            from vendors vv
           where vv.erp_id = o.supplier_code
           order by vv.is_active desc nulls last, vv.updated_at desc nulls last, vv.id
           limit 1
      ) v on true
),
bucketed as (
    select t.*,
           {_BUCKET_CASE} as bucket,
           case when t.due_date is not null then t.due_date - current_date end as days_to_due
      from termed t
)
"""


async def summary(db: AsyncSession, currency: str | None = None) -> dict:
    """Per bucket: how many documents, how much money, over what date range."""
    params: dict = {}
    clause = ""
    if currency:
        clause = " where currency = :ccy"
        params["ccy"] = currency
    rows = (await db.execute(text(_BASE + f"""
        select currency, bucket,
               count(*) as bills,
               coalesce(sum(open_bal), 0) as amount,
               min(due_date) as first_due,
               max(due_date) as last_due,
               count(*) filter (where no_vendor_record) as bills_without_vendor
          from bucketed {clause}
         group by currency, bucket
    """), params)).mappings().all()

    by_ccy: dict[str, dict] = {}
    for r in rows:
        ccy = r["currency"] or "(unknown)"
        by_ccy.setdefault(ccy, {})[r["bucket"]] = r

    out = []
    for ccy in sorted(by_ccy):
        got = by_ccy[ccy]
        buckets = [{
            "key": k,
            "label": BUCKET_LABELS[k],
            "overdue": k in OVERDUE_KEYS,
            "bills": int(got[k]["bills"]) if k in got else 0,
            "amount": str(got[k]["amount"]) if k in got else "0",
            "first_due": got[k]["first_due"].isoformat()
            if k in got and got[k]["first_due"] else None,
            "last_due": got[k]["last_due"].isoformat()
            if k in got and got[k]["last_due"] else None,
        } for k in BUCKET_KEYS]
        total = sum(float(b["amount"]) for b in buckets)
        overdue = sum(float(b["amount"]) for b in buckets if b["overdue"])
        undated = next(b for b in buckets if b["key"] == UNDATED)
        forward = total - overdue - float(undated["amount"])
        out.append({
            "currency": ccy,
            "buckets": buckets,
            # Stated as three numbers that add to the total, so the page can
            # never show a "cash needed" figure that quietly includes money it
            # has no date for.
            "total": f"{total:.2f}",
            "overdue": f"{overdue:.2f}",
            "undated": undated["amount"],
            "undated_bills": undated["bills"],
            "forward": f"{forward:.2f}",
        })
    return {"currencies": out, "as_of": date.today().isoformat()}


# Click-to-sort whitelist for items(): key -> SQL over the `bucketed` CTE.
# Fragments written here (the request key is only looked up), each sorting what
# that column of ApCashFlowPage shows.
ITEM_SORT = {
    "bill_no": "bill_no",
    "supplier": "lower(coalesce(nullif(supplier_name, ''), supplier_code))",
    "invoice_no": "lower(nullif(invoice_no, ''))",
    "purchase_order": "lower(nullif(purchase_order, ''))",
    "bill_date": "bill_date",
    "payment_terms": "lower(nullif(payment_terms, ''))",
    "due_date": "due_date",
    "open_bal": "open_bal",
}
ITEM_TIEBREAK = "bill_no"


async def items(db: AsyncSession, currency: str, bucket: str | None = None,
                supplier_code: str | None = None,
                limit: int = 500, offset: int = 0, order: str | None = None) -> dict:
    """The documents behind a bucket, soonest due first — which is also the
    order they have to be paid in.

    Every figure on this page is clickable through to this, including a single
    supplier in the undated list: a forecast nobody can trace back to documents
    is a number people quietly stop believing.
    """
    params: dict = {"ccy": currency, "limit": max(1, min(limit, 2000)),
                    "offset": max(0, offset)}
    where = ["currency = :ccy"]
    if bucket:
        where.append("bucket = :bucket")
        params["bucket"] = bucket
    if supplier_code:
        where.append("supplier_code = :supplier_code")
        params["supplier_code"] = supplier_code
    clause = " where " + " and ".join(where)
    rows = (await db.execute(text(_BASE + f"""
        select bill_no, currency, bill_date, supplier_code, supplier_name,
               invoice_lines, trade_type, open_bal,
               payment_terms, term_days, due_date, days_to_due, bucket,
               no_vendor_record
          from bucketed {clause}
         -- Undated rows sort last within a bucket: they have no date to sort
         -- by, and biggest-first is the useful order for them.
         order by {order or "due_date nulls last, open_bal desc, bill_no"}
         limit :limit offset :offset
    """), params)).mappings().all()
    total = (await db.execute(text(
        _BASE + f" select count(*), coalesce(sum(open_bal), 0) from bucketed {clause}"),
        params)).one()
    return {
        "total": int(total[0]),
        "amount": str(total[1]),
        "items": [{
            "bill_no": r["bill_no"],
            "currency": r["currency"],
            "bill_date": r["bill_date"].isoformat() if r["bill_date"] else None,
            "supplier_code": r["supplier_code"],
            "supplier_name": r["supplier_name"],
            "invoices": invoices(r["invoice_lines"]),
            "trade_type": r["trade_type"],
            "open_bal": str(r["open_bal"]),
            "payment_terms": r["payment_terms"],
            "term_days": r["term_days"],
            "due_date": r["due_date"].isoformat() if r["due_date"] else None,
            "days_to_due": r["days_to_due"],
            "bucket": r["bucket"],
            "bucket_label": BUCKET_LABELS.get(r["bucket"], r["bucket"]),
            # The two reasons a row can be undated, told apart: no supplier
            # record at all, versus a record carrying a term we do not know.
            "no_vendor_record": r["no_vendor_record"],
        } for r in rows],
    }


async def missing_terms(db: AsyncSession, currency: str | None = None) -> dict:
    """The suppliers holding up the forecast, biggest first.

    This is a work list, not a statistic: every supplier here is one vendor
    record (or one payment-term setting) away from being forecastable, and
    together they are 38% of the open balance.
    """
    params: dict = {}
    clause = " where bucket = '" + UNDATED + "'"
    if currency:
        clause += " and currency = :ccy"
        params["ccy"] = currency
    rows = (await db.execute(text(_BASE + f"""
        select supplier_code, currency,
               max(supplier_name) as supplier_name,
               bool_and(no_vendor_record) as no_vendor_record,
               max(payment_terms) as payment_terms,
               count(*) as bills,
               coalesce(sum(open_bal), 0) as amount,
               min(bill_date) as oldest_bill,
               max(bill_date) as newest_bill
          from bucketed {clause}
         group by supplier_code, currency
         order by sum(open_bal) desc
    """), params)).mappings().all()

    # The date carrying the most undated money, and how much of it.
    #
    # Measured before this existed: 25 of the 53 undated CAD bills share one
    # date, 2020-08-31, and carry 2,475,876.92 — 74% of the undated balance. An
    # opening-balance load at NC go-live, not 39 suppliers waiting to be set up.
    # Without saying so, this page sends finance off to create vendor records
    # for money that should be judged on AP Subledger Health instead. The date
    # is DERIVED, never hard-coded: a go-live date belongs to the data, not to
    # this file, and the same cluster in a different company would be a
    # different day.
    cluster = (await db.execute(text(_BASE + f"""
        select bill_date, count(*) as bills, coalesce(sum(open_bal), 0) as amount
          from bucketed {clause} and bill_date is not null
         group by bill_date order by sum(open_bal) desc limit 1
    """), params)).mappings().first()

    return {"total": len(rows),
            "largest_single_date": {
                "bill_date": cluster["bill_date"].isoformat(),
                "bills": int(cluster["bills"]),
                "amount": str(cluster["amount"]),
            } if cluster else None,
            "items": [{
        "supplier_code": r["supplier_code"],
        "supplier_name": r["supplier_name"],
        "currency": r["currency"],
        # Two different fixes: create the vendor, or set its term.
        "reason": "no_vendor_record" if r["no_vendor_record"] else "unrecognised_term",
        "payment_terms": r["payment_terms"],
        "bills": int(r["bills"]),
        "amount": str(r["amount"]),
        "oldest_bill": r["oldest_bill"].isoformat() if r["oldest_bill"] else None,
        "newest_bill": r["newest_bill"].isoformat() if r["newest_bill"] else None,
    } for r in rows]}
