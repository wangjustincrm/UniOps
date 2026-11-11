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

Two rules finance set on 2026-11-02 decide what counts at all:

  **Only bills an approved EPMS PA is about to pay.** An open NC line is cash
  flow when its invoice is on a PA in `approved` — not drafted or in review
  (no commitment yet), not `processed` (already paid; NC catches up later).
  Measured that day: 525,334.73 of 2,143,856.51 CAD open, and 285,284.58 more
  sat on processed PAs that NC had not yet recorded as paid.

  **Reversal pairs net out.** A reversal posted but never offset against its
  original in NC leaves both open. Lines with the same supplier, currency and
  invoice number whose open balances add to zero are left out (8 pairs, 16
  bills that day; none was on an approved PA, so the rule above already drops
  them — this keeps a future one from splitting across two buckets).

**Due dates are the EPMS invoice's** (finance, 2026-11-11). NC's bill date is
when the payable was keyed in NC, not when the supplier issued the invoice, so
NC bill date + vendor term put almost every bill "due in 8-30 days". Every
counted line already reached an EPMS invoice through the match above, so its
invoice date and due date come from there, and the term shown is the days
between the two. Measured that day on the same 227 lines: totals unchanged,
but CAD overdue went from 9,425.51 to 342,688.21. A bill whose invoices fall
due on different days is split into one row per due date (6 of 203 bills).
NC bill date + vendor term is kept only as a fallback for an invoice with no
due date, which EPMS does not allow, so undated bills should no longer occur.

Two kinds of approved money never reach NC as a payable, so the NC route above
cannot see them, and they are added as rows of their own (finance, 2026-11-05):

  **Prepayment PAs.** A prepayment has no invoice yet, so there is no NC line to
  find it through. Measured that day: all 18 approved ones, 7,907.14 CAD and
  6,869.05 USD. They count at `payment_amount`.

  **Expense claims.** An employee is paid from the claim itself; NC has no
  payable for it. The same set as the Payment Batches due list: approved, Travel
  Applications excluded. They count at `total_amount`.

Neither has a bill date or a vendor term, and both are payable once approved
(the same 0 days as a `prepayment` vendor term), so the approval date stands in
as the bill date and the due date. `expected_settlement_date` on a prepayment
is when it should settle against an invoice, not when it gets paid. Neither can
ever be undated, so neither reaches the missing-terms work list.

The figures in the three findings above were measured before those rules, on
every approved NC bill.

Currency is never mixed. Everything is derived on read — there is no forecast
table to go stale.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.payment_execute import NON_PAYABLE_CLAIM_TYPES
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

PA_APPROVED = "approved"
CLAIM_APPROVED = "approved"
PA_PREPAYMENT = "prepayment"
# Where a row's due date came from (`due_from`): the EPMS invoice (every NC
# bill row, since 2026-11-11), NC bill date + vendor term (fallback for a bill
# row whose invoice has no due date), or the approval date (prepayment, claim).
DUE_FROM_INVOICE = "invoice"
DUE_FROM_TERM = "vendor_term"
DUE_FROM_APPROVAL = "approval"
_NON_PAYABLE_SQL = ", ".join(f"'{t}'" for t in NON_PAYABLE_CLAIM_TYPES)
# The AP <-> NC invoice-number match (nc_push.match_invoice_no and its SQL twin
# LINE_MATCH_SQL): upper-case, no whitespace, no hyphens.
INV_KEY_NC = "nullif(replace(l.invoice_no_norm, '-', ''), '')"
INV_KEY_EPMS = (r"nullif(replace(upper(regexp_replace("
                r"coalesce(i.vendor_invoice_number, ''), '\s', '', 'g')), '-', ''), '')")

_DAYS_SQL = "(t.due_date - current_date)"
_BUCKET_CASE = ("case when t.due_date is null then '" + UNDATED + "' " + " ".join(
    f"when {pred.format(d=_DAYS_SQL)} then '{key}'"
    for key, _, pred in BUCKETS if pred) + f" else '{BUCKETS[-1][0]}' end")

_BASE = f"""
with dismissed as (
    select bill_no from nc_ap_bill_dismissals where restored_at is null
),
open_lines as (
    -- `money_bal` is the open amount per LINE, and a bill carries one invoice
    -- per line, often several, so everything below is decided per line.
    select l.*, b.currency, b.trade_type as bill_trade_type,
           {INV_KEY_NC} as inv_key
      from nc_ap_bill_lines l
      join nc_ap_bills b on b.id = l.bill_id
     where b.bill_status = 1 and b.approve_status = 1
       and l.money_bal <> 0
       -- Bills finance has judged not to be real debt never reach the
       -- forecast. That is the whole point of having judged them.
       and not exists (select 1 from dismissed x where x.bill_no = l.bill_no)
),
-- A reversal finance posted but never offset against its original in NC:
-- same supplier, currency and invoice number, open balances that add to zero.
-- Both stay open in NC; neither is money to pay (finance, 2026-11-02).
offset_pairs as (
    select supplier_code, currency, inv_key
      from open_lines
     where inv_key is not null
     group by supplier_code, currency, inv_key
    having count(*) > 1 and sum(money_bal) = 0
),
-- Only what an approved EPMS PA is about to pay is cash flow (finance,
-- 2026-11-02). `processed` is already paid; earlier states are not yet a
-- commitment. The NC line is found the way the AP list finds it: invoice
-- number ignoring case, spaces and hyphens, for the same NC supplier.
approved_pa as (
    select distinct p.pa_number, v.erp_id, {INV_KEY_EPMS} as inv_key,
           i.invoice_date, i.due_date
      from payment_applications p
      cross join lateral jsonb_array_elements_text(p.invoice_ids) x(invoice_id)
      join invoices i on i.id::text = x.invoice_id
      join vendors v on v.id = i.vendor_id
     where p.status = '{PA_APPROVED}'
       -- A PA paid invoice by invoice stays approved until the last one.
       and i.status <> 'paid'
),
line_pa as (
    select o.id as line_id, o.bill_no, a.pa_number, a.invoice_date, a.due_date
      from open_lines o
      join approved_pa a on a.inv_key = o.inv_key and a.erp_id = o.supplier_code
     where not exists (select 1 from offset_pairs k
                        where k.supplier_code = o.supplier_code
                          and k.currency = o.currency and k.inv_key = o.inv_key)
),
-- The dates a line is paid by are the EPMS invoice's, not NC's (finance,
-- 2026-11-11): NC's bill date is when the payable was keyed, not when the
-- supplier issued the invoice. Every counted line reached an EPMS invoice
-- above, and EPMS requires a due date, so every counted line has one. A line
-- matching two invoices (none did when this was written) takes the earlier.
line_due as (
    select line_id, min(invoice_date) as invoice_date, min(due_date) as due_date
      from line_pa
     group by line_id
),
open_bills as (
    -- One row per NC document and invoice due date, over its PA-linked lines
    -- only. A bill carrying invoices due on different days is split, so each
    -- amount lands in its own bucket (6 of 203 bills on 2026-11-11). Everything
    -- else on a payable is a header property and is taken with min() rather
    -- than repeated per line.
    select l.bill_no, l.currency,
           -- Unique across rows: the AP number, plus the due date when the
           -- bill had to be split.
           case when count(*) over (partition by l.bill_no, l.currency) > 1
                then l.bill_no || ' / ' || d.due_date::text
                else l.bill_no end as doc_key,
           d.due_date as invoice_due_date,
           min(d.invoice_date) as invoice_date,
           min(l.bill_date)::date as bill_date,
           min(l.supplier_code) as supplier_code,
           min(l.supplier_name) as supplier_name,
           -- Sort key only: a bill carries one invoice per line, often
           -- several. What the page shows is `invoice_lines`.
           min(l.invoice_no) as invoice_no,
           min(l.purchase_order) as purchase_order,
           {lines_agg()} as invoice_lines,
           min(l.bill_trade_type) as trade_type,
           sum(l.money_bal) as open_bal,
           -- Search text only: every invoice number on the bill's lines.
           string_agg(l.invoice_no, ' ') as invoice_search,
           (select string_agg(distinct p.pa_number, ', ' order by p.pa_number)
              from line_pa p join line_due pd on pd.line_id = p.line_id
             where p.bill_no = l.bill_no
               and pd.due_date is not distinct from d.due_date) as pa_numbers
      from open_lines l
      join line_due d on d.line_id = l.id
     group by l.bill_no, l.currency, d.due_date
),
termed as (
    -- Explicit columns: `docs` below unions by position.
    select o.doc_key, o.bill_no, o.currency, o.bill_date, o.invoice_date,
           o.supplier_code, o.supplier_name, o.invoice_no, o.purchase_order,
           o.invoice_lines, o.trade_type, o.open_bal, o.invoice_search, o.pa_numbers,
           v.payment_terms,
           -- The term as the invoice states it, when it does.
           case when o.invoice_due_date is not null and o.invoice_date is not null
                then o.invoice_due_date - o.invoice_date
                else {_TERM_CASE} end as term_days,
           -- Fallback only (an EPMS invoice always has a due date): NC's bill
           -- date plus the vendor master term, as before 2026-11-11.
           coalesce(o.invoice_due_date,
                    case when o.bill_date is not null and {_TERM_CASE} is not null
                         then o.bill_date + ({_TERM_CASE}) end) as due_date,
           case when o.invoice_due_date is not null then '{DUE_FROM_INVOICE}'
                else '{DUE_FROM_TERM}' end as due_from,
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
-- What is approved to pay but has no NC payable to be found through. Columns
-- line up with `termed`. A claim's number rides in `pa_numbers`: that column is
-- "the approved document behind this row", and search and sort already use it.
docs as (
    select 'bill' as doc_kind, t.* from termed t
    union all
    select '{PA_PREPAYMENT}', p.pa_number,
           null, p.currency, coalesce(p.approved_at, p.created_at)::date,
           coalesce(p.approved_at, p.created_at)::date,
           v.erp_id, p.vendor_name, null, p.po_number,
           json_build_array(json_build_object('purchase_order', p.po_number)),
           null, p.payment_amount, null, p.pa_number,
           '{PA_PREPAYMENT}', 0, coalesce(p.approved_at, p.created_at)::date,
           '{DUE_FROM_APPROVAL}', false
      from payment_applications p
      left join vendors v on v.id = p.vendor_id
     where p.status = '{PA_APPROVED}' and p.pa_type = '{PA_PREPAYMENT}'
       -- Already counted at its NC balance if an invoice has since been linked.
       and p.pa_number not in (select pa_number from line_pa)
    union all
    select 'claim', c.claim_number,
           null, c.currency, coalesce(c.approved_at, c.created_at)::date,
           coalesce(c.approved_at, c.created_at)::date,
           null, c.employee_name, null, null,
           null, null, c.total_amount, null, c.claim_number,
           'on approval', 0, coalesce(c.approved_at, c.created_at)::date,
           '{DUE_FROM_APPROVAL}', false
      from expense_claims c
     where c.status = '{CLAIM_APPROVED}' and c.claim_type not in ({_NON_PAYABLE_SQL})
),
bucketed as (
    select t.*,
           {_BUCKET_CASE} as bucket,
           case when t.due_date is not null then t.due_date - current_date end as days_to_due
      from docs t
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
    "invoice_date": "invoice_date",
    # The Term column shows days (invoice date to due date for a bill).
    "payment_terms": "term_days",
    "due_date": "due_date",
    "open_bal": "open_bal",
    "pa": "pa_numbers",
}
ITEM_TIEBREAK = "doc_key"


async def items(db: AsyncSession, currency: str, bucket: str | None = None,
                supplier_code: str | None = None, q: str | None = None,
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
    if q and q.strip():
        # Bill no, any invoice number on it, supplier name or code, PA number.
        where.append("concat_ws(' ', bill_no, invoice_search, supplier_name, supplier_code,"
                     " pa_numbers) ilike :q escape '\\'")
        params["q"] = "%" + (q.strip().replace("\\", "\\\\").replace("%", "\\%")
                             .replace("_", "\\_")) + "%"
    clause = " where " + " and ".join(where)
    rows = (await db.execute(text(_BASE + f"""
        select doc_kind, doc_key, bill_no, currency, bill_date, invoice_date,
               supplier_code, supplier_name,
               invoice_lines, trade_type, open_bal, pa_numbers,
               payment_terms, term_days, due_date, due_from, days_to_due, bucket,
               no_vendor_record
          from bucketed {clause}
         -- Undated rows sort last within a bucket: they have no date to sort
         -- by, and biggest-first is the useful order for them.
         order by {order or "due_date nulls last, open_bal desc, doc_key"}
         limit :limit offset :offset
    """), params)).mappings().all()
    total = (await db.execute(text(
        _BASE + f" select count(*), coalesce(sum(open_bal), 0) from bucketed {clause}"),
        params)).one()
    return {
        "total": int(total[0]),
        "amount": str(total[1]),
        "items": [{
            # bill | prepayment | claim. Only a bill has a bill_no; doc_key is
            # unique across all three (a bill split by invoice due date shows
            # the same bill_no on two rows, with two doc_keys).
            "doc_kind": r["doc_kind"],
            "doc_key": r["doc_key"],
            "bill_no": r["bill_no"],
            "currency": r["currency"],
            # NC's bill date — kept for reference; it no longer decides anything
            # for a bill row. What the page shows is invoice_date.
            "bill_date": r["bill_date"].isoformat() if r["bill_date"] else None,
            # EPMS invoice date for a bill; approval date for prepayment/claim.
            "invoice_date": r["invoice_date"].isoformat() if r["invoice_date"] else None,
            "supplier_code": r["supplier_code"],
            "supplier_name": r["supplier_name"],
            "invoices": invoices(r["invoice_lines"]),
            "trade_type": r["trade_type"],
            "open_bal": str(r["open_bal"]),
            # The approved PA(s) that put this bill on the forecast, or the
            # prepayment PA / claim that is the row.
            "pa_numbers": r["pa_numbers"].split(", ") if r["pa_numbers"] else [],
            "payment_terms": r["payment_terms"],
            "term_days": r["term_days"],
            "due_date": r["due_date"].isoformat() if r["due_date"] else None,
            "due_from": r["due_from"],
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
