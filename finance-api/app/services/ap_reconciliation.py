"""AP reconciliation — where each of our invoices stands in NC.

This is the first layer above the NC mirror and it answers the question nobody
could answer before: for every payable we know about, is it on NC's books, is
it still open there, or did it never arrive?

Since NC is the book of record for balances (spec §10.1), "still open" is read
from `nc_ap_bill_lines.money_bal`, never from our own `paid_amount` — payments
are made in NC and never flow back, which is why 31 of our invoices worth
129,023.71 were still showing as unpaid on our side when NC had settled them.

Everything here is DERIVED ON READ. There is no reconciliation table to fall
out of date: the mirror is the state, this is a view over it.

Three rules that come straight from how NC actually stores things:

* **Match on the normalised invoice number, plus the supplier when we know it.**
  `vendors.erp_id` maps 700-for-700 onto `BD_SUPPLIER.code` (verified
  2026-09-21), so supplier is a real join key here, not a name comparison.
* **"No match" is never a conclusion on its own.** Before calling an invoice
  missing, look for the same supplier and the same amount under a *different*
  invoice number — that is how the two real NC keying errors were found
  (IN-22-1014809 keyed as IN-22-1011569; 508483 keyed as 50843). A checker
  that skips this step reports typos as missing money.
* **Drafts are not findings.** A draft that is not in NC is a draft, not a gap.
  The categories keep them separate rather than padding the alarm count.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Category keys — stable, used by the API and the UI.
IN_NC_OPEN = "in_nc_open"                  # NC has it, still unpaid there →真欠
IN_NC_SETTLED = "in_nc_settled"            # NC has it and settled it
NO_MATCH = "missing_in_nc"                 # not in NC under any key we can find
INVOICE_NO_MISMATCH = "invoice_no_mismatch"  # same supplier + amount, different number
NOT_SUBMITTED = "not_submitted"            # our draft — expected to be absent
IN_NC_UNAPPROVED = "in_nc_unapproved"      # NC has a document, but never approved it

# Amount tolerance when matching by money instead of by number. NC and we both
# store 2dp, so this is a guard against representation noise, not fuzziness.
AMOUNT_EPSILON = "0.01"

# How far apart the two documents may be dated before "same supplier, same
# amount" stops being evidence of a keying error. Recurring charges — a monthly
# service fee, a standing rental — are the same amount every period, so without
# this the check reports every one of them as a typo. 120 days is wide enough to
# cover a late-keyed invoice and narrow enough to exclude the next cycle.
AMOUNT_MATCH_MAX_DAY_GAP = 120


@dataclass(frozen=True)
class ReconFilters:
    date_from: date | None = None
    date_to: date | None = None
    include_paid: bool = True

    def clause(self) -> tuple[str, dict]:
        where = ["i.status <> 'void'"]
        params: dict = {}
        if not self.include_paid:
            where.append("i.status <> 'paid'")
        if self.date_from:
            where.append("i.invoice_date >= :date_from")
            params["date_from"] = self.date_from
        if self.date_to:
            where.append("i.invoice_date <= :date_to")
            params["date_to"] = self.date_to
        return " and ".join(where), params


# The normalisation must match services/nc_ap_sync.normalise_invoice_no exactly,
# or half the mirror becomes unmatchable. Both sides: upper-case, all whitespace
# removed, empty treated as unknown.
_OURS_NORM = r"nullif(upper(regexp_replace(coalesce(i.vendor_invoice_number,''), '\s', '', 'g')), '')"

_BASE = f"""
with ours as (
    select i.id, i.ap_invoice_number, i.vendor_invoice_number,
           {_OURS_NORM} as inv_norm,
           v.erp_id, i.vendor_name, i.total_amount, i.paid_amount,
           i.invoice_date, i.due_date, i.status, i.po_number, i.source
      from ap_invoices i
      left join vendors v on v.id = i.vendor_id
     where {{where}}
),
-- One row per (invoice number, supplier) as NC holds it. Grouped because a
-- single NC bill can carry the same invoice number on several lines.
-- Only documents NC actually stands behind. Matching against an unapproved
-- draft would report our invoice as "on NC's books" when NC never accepted it;
-- 13 of 149 matches were exactly that before this filter existed.
nc_inv as (
    select l.invoice_no_norm, l.supplier_code,
           sum(l.money_cr) as nc_money_cr,
           sum(l.money_bal) as nc_money_bal,
           min(l.bill_no) as nc_bill_no,
           count(*) as nc_lines
      from nc_ap_bill_lines l
      join nc_ap_bills b on b.id = l.bill_id
     where l.invoice_no_norm is not null
       and b.bill_status = 1 and b.approve_status = 1
     group by l.invoice_no_norm, l.supplier_code
),
-- The same lookup over the documents NC did NOT approve. An invoice that only
-- matches here is a real finding of its own: finance raised the document and
-- then abandoned it, so the liability is neither on the books nor chased.
nc_inv_unapproved as (
    select l.invoice_no_norm, l.supplier_code, min(l.bill_no) as nc_bill_no
      from nc_ap_bill_lines l
      join nc_ap_bills b on b.id = l.bill_id
     where l.invoice_no_norm is not null
       and not (b.bill_status = 1 and b.approve_status = 1)
     group by l.invoice_no_norm, l.supplier_code
),
-- distinct on keeps this to exactly ONE row per invoice of ours. Without it a
-- number reused across suppliers would fan out and inflate every count in the
-- summary. Ordering puts the supplier that actually agrees first.
matched as (
    select distinct on (o.id) o.id,
           n.nc_bill_no, n.nc_money_cr, n.nc_money_bal, n.nc_lines,
           (o.erp_id is not null and n.supplier_code = o.erp_id) as supplier_agrees
      from ours o
      join nc_inv n on n.invoice_no_norm = o.inv_norm
     order by o.id,
              (o.erp_id is not null and n.supplier_code = o.erp_id) desc,
              n.nc_bill_no
),
-- Judgement 2: the invoice is not findable by number, but the same supplier has
-- a line for exactly the same money. That is an NC keying error, not a gap.
by_amount as (
    select distinct on (o.id) o.id,
           l.bill_no as alt_bill_no,
           l.invoice_no as alt_invoice_no,
           l.money_cr as alt_money_cr,
           l.money_bal as alt_money_bal,
           l.bill_date::date as alt_bill_date,
           abs(l.bill_date::date - o.invoice_date) as alt_day_gap
      from ours o
      join nc_ap_bill_lines l
        on l.supplier_code = o.erp_id
      join nc_ap_bills ab
        on ab.id = l.bill_id and ab.bill_status = 1 and ab.approve_status = 1
       and abs(l.money_cr - o.total_amount) <= {AMOUNT_EPSILON}
       and o.invoice_date is not null
       and l.bill_date is not null
       and abs(l.bill_date::date - o.invoice_date) <= {AMOUNT_MATCH_MAX_DAY_GAP}
     where o.erp_id is not null
       and not exists (select 1 from matched m where m.id = o.id)
     order by o.id, abs(l.bill_date::date - o.invoice_date)
),
unapproved as (
    select distinct on (o.id) o.id, n.nc_bill_no as unapproved_bill_no
      from ours o
      join nc_inv_unapproved n on n.invoice_no_norm = o.inv_norm
     where not exists (select 1 from matched m where m.id = o.id)
     order by o.id, n.nc_bill_no
),
classified as (
    select o.*, u.unapproved_bill_no,
           m.nc_bill_no, m.nc_money_cr, m.nc_money_bal, m.nc_lines, m.supplier_agrees,
           a.alt_bill_no, a.alt_invoice_no, a.alt_money_cr, a.alt_money_bal,
           a.alt_bill_date, a.alt_day_gap,
           case
             when o.status = 'draft'              then '{NOT_SUBMITTED}'
             when m.id is not null
                  and coalesce(m.nc_money_bal, 0) <> 0 then '{IN_NC_OPEN}'
             when m.id is not null                then '{IN_NC_SETTLED}'
             when a.id is not null                then '{INVOICE_NO_MISMATCH}'
             when u.id is not null                then '{IN_NC_UNAPPROVED}'
             else '{NO_MATCH}'
           end as category
      from ours o
      left join matched m on m.id = o.id
      left join by_amount a on a.id = o.id
      left join unapproved u on u.id = o.id
)
"""


async def summary(db: AsyncSession, f: ReconFilters) -> dict:
    """The first screen: how much money sits in each state."""
    where, params = f.clause()
    sql = _BASE.format(where=where) + """
    select category,
           count(*) as invoices,
           coalesce(sum(total_amount), 0) as our_total,
           coalesce(sum(total_amount - coalesce(paid_amount, 0)), 0) as our_outstanding,
           coalesce(sum(nc_money_bal), 0) as nc_open
      from classified
     group by category
    """
    rows = (await db.execute(text(sql), params)).mappings().all()
    by_cat = {r["category"]: dict(r) for r in rows}
    out = []
    for key in (NO_MATCH, IN_NC_UNAPPROVED, INVOICE_NO_MISMATCH, IN_NC_OPEN,
                IN_NC_SETTLED, NOT_SUBMITTED):
        r = by_cat.get(key)
        out.append({
            "category": key,
            "invoices": int(r["invoices"]) if r else 0,
            "our_total": str(r["our_total"]) if r else "0",
            "our_outstanding": str(r["our_outstanding"]) if r else "0",
            "nc_open": str(r["nc_open"]) if r else "0",
        })
    return {"categories": out}


async def items(db: AsyncSession, f: ReconFilters, category: str,
                limit: int = 200, offset: int = 0) -> dict:
    """The drill-down. Ordered by money, because that is the order they get
    worked in."""
    where, params = f.clause()
    params |= {"category": category, "limit": limit, "offset": offset}
    sql = _BASE.format(where=where) + """
    select id, ap_invoice_number, vendor_invoice_number, vendor_name, erp_id,
           total_amount, paid_amount, invoice_date, due_date, status, po_number,
           source, category, nc_bill_no, nc_money_cr, nc_money_bal, nc_lines,
           supplier_agrees, alt_bill_no, alt_invoice_no, alt_money_cr, alt_money_bal,
           alt_bill_date, alt_day_gap, unapproved_bill_no
      from classified
     where category = :category
     order by total_amount desc nulls last
     limit :limit offset :offset
    """
    rows = (await db.execute(text(sql), params)).mappings().all()
    count_sql = _BASE.format(where=where) + \
        " select count(*) as n from classified where category = :category"
    total = (await db.execute(text(count_sql), params)).scalar_one()
    return {"total": int(total), "items": [_item_out(r) for r in rows]}


def _item_out(r) -> dict:
    def s(v):
        return None if v is None else str(v)
    return {
        "id": str(r["id"]),
        "ap_invoice_number": r["ap_invoice_number"],
        "vendor_invoice_number": r["vendor_invoice_number"],
        "vendor_name": r["vendor_name"],
        "vendor_erp_id": r["erp_id"],
        "total_amount": s(r["total_amount"]),
        "paid_amount": s(r["paid_amount"]),
        "invoice_date": r["invoice_date"].isoformat() if r["invoice_date"] else None,
        "due_date": r["due_date"].isoformat() if r["due_date"] else None,
        "status": r["status"],
        "po_number": r["po_number"],
        "source": r["source"],
        "category": r["category"],
        "unapproved_bill_no": r["unapproved_bill_no"],
        "nc": {
            "bill_no": r["nc_bill_no"],
            "money_cr": s(r["nc_money_cr"]),
            "money_bal": s(r["nc_money_bal"]),
            "lines": r["nc_lines"],
            "supplier_agrees": r["supplier_agrees"],
        } if r["nc_bill_no"] else None,
        # Only present for invoice_no_mismatch: what NC appears to have keyed
        # instead. Shown so a human confirms before anyone edits NC.
        "nc_amount_match": {
            "bill_no": r["alt_bill_no"],
            "invoice_no": r["alt_invoice_no"],
            "money_cr": s(r["alt_money_cr"]),
            "money_bal": s(r["alt_money_bal"]),
            "bill_date": r["alt_bill_date"].isoformat() if r["alt_bill_date"] else None,
            # How far apart the two documents are dated. Small gap = almost
            # certainly the same invoice keyed wrong; near the limit = judge it.
            "day_gap": r["alt_day_gap"],
        } if r["alt_bill_no"] else None,
    }


async def date_anomalies(db: AsyncSession, f: ReconFilters) -> dict:
    """Judgement 6: invoice dates that cannot be right.

    A due date before the invoice date, or an invoice dated in the future, is
    almost always a day/month swap out of OCR. It matters because both feed the
    cash-flow buckets directly: a September invoice read as December moves real
    money into a month it will never be paid in.
    """
    where, params = f.clause()
    sql = f"""
    select i.id, i.ap_invoice_number, i.vendor_name, i.vendor_invoice_number,
           i.total_amount, i.invoice_date, i.due_date, i.status,
           case when i.due_date < i.invoice_date then 'due_before_invoice'
                else 'invoice_in_future' end as reason
      from ap_invoices i
     where {where}
       and (i.due_date < i.invoice_date or i.invoice_date > current_date)
     order by i.total_amount desc nulls last
    """
    rows = (await db.execute(text(sql), params)).mappings().all()
    return {"total": len(rows), "items": [{
        "id": str(r["id"]),
        "ap_invoice_number": r["ap_invoice_number"],
        "vendor_name": r["vendor_name"],
        "vendor_invoice_number": r["vendor_invoice_number"],
        "total_amount": str(r["total_amount"]) if r["total_amount"] is not None else None,
        "invoice_date": r["invoice_date"].isoformat() if r["invoice_date"] else None,
        "due_date": r["due_date"].isoformat() if r["due_date"] else None,
        "status": r["status"],
        "reason": r["reason"],
    } for r in rows]}
