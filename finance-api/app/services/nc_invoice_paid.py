"""Has NC already paid this EPMS invoice? (Payment Batch "NC Paid", 2026-11-11)

Finance pays in NC; the UniOps PA is closed by hand afterwards, and sometimes
never is (PA-20260817-0009 sat approved for six weeks after NC paid it on
2026-09-26). A PA like that is still in the payment run, one click from being
paid twice. This module answers, per EPMS invoice, what NC's books say.

How an invoice is found in NC: the same key the AP list and AP Cash Flow use —
invoice number upper-cased, no whitespace, no hyphens — on an approved NC AP
bill (bill_status=1, approve_status=1) of the vendor's NC supplier code
(vendors.erp_id). Drafts are not NC's position and are ignored.

Whether it is paid is read from the NC bill LINE's open balance (money_bal):
NC payment documents point at the bill (top_bill_id), never at the line, and
one bill routinely carries several invoices — 9 of the 186 open invoices on
the due list on 2026-11-11 sat on a bill that already had a payment against
other lines. So the balance decides, and the payment numbers are shown only as
the evidence that sits on the same bill.

Lines on a bill dated more than FALSE_MATCH_DAYS before the invoice are not
matches: utilities reuse account-style numbers, and Hydro One's
PA-20261026-0005 "matched" a 2021 bill that is long settled — reading that as
"paid in NC" would tell finance to skip a real 71K payable.
"""
import uuid
from decimal import Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ap_cash_flow import INV_KEY_EPMS, INV_KEY_NC

FALSE_MATCH_DAYS = 60

PAID = "paid"            # balance 0 and an NC payment on the bill
CLOSED = "closed"        # balance 0 with no NC payment (offset / write-off)
PARTIAL = "partial"      # some of it paid
OPEN = "open"            # on NC's books, nothing paid
NOT_IN_NC = "not_in_nc"  # no approved NC bill line for it
SETTLED = (PAID, CLOSED)

_SQL = text(f"""
with inv as (
    select i.id, i.invoice_date, v.erp_id, {INV_KEY_EPMS} as inv_key
      from invoices i
      left join vendors v on v.id = i.vendor_id
     where i.id in :ids
),
lines as (
    select inv.id, l.bill_no, b.nc_pk as bill_pk, l.money_cr, l.money_bal
      from inv
      join nc_ap_bill_lines l
        on {INV_KEY_NC} = inv.inv_key and l.supplier_code = inv.erp_id
      join nc_ap_bills b
        on b.id = l.bill_id and b.bill_status = 1 and b.approve_status = 1
     where inv.inv_key is not null
       and (l.bill_date is null or inv.invoice_date is null
            or l.bill_date::date >= inv.invoice_date - {FALSE_MATCH_DAYS})
),
pays as (
    select distinct x.id, p.bill_no as pay_no,
           coalesce(p.pay_date, p.approve_date, p.bill_date)::date as pay_date
      from (select distinct id, bill_pk from lines) x
      join nc_ap_payment_lines pl on pl.top_bill_id = x.bill_pk
      join nc_ap_payments p
        on p.id = pl.payment_id and p.bill_status = 1 and p.approve_status = 1
)
select g.id, g.bills, g.cr, g.bal, g.open_lines,
       (select string_agg(distinct pay_no, ', ') from pays where pays.id = g.id) as pay_nos,
       (select max(pay_date) from pays where pays.id = g.id) as paid_on
  from (select id, string_agg(distinct bill_no, ', ') as bills,
               sum(money_cr) as cr, sum(money_bal) as bal,
               count(*) filter (where money_bal <> 0) as open_lines
          from lines group by id) g
""").bindparams(bindparam("ids", expanding=True))


def _status(cr: Decimal, bal: Decimal, open_lines: int, has_pay: bool) -> str:
    if open_lines == 0:
        return PAID if has_pay else CLOSED
    if (cr or 0) != 0 and bal != cr:
        return PARTIAL
    return OPEN


async def invoice_status(db: AsyncSession, invoice_ids) -> dict[str, dict]:
    """{invoice id: {status, bill_no, payment_no, paid_on, open_balance}} for
    EPMS invoices; every id asked for is in the result (NOT_IN_NC when NC has
    nothing). Ids that are not EPMS invoices (OA Direct PAs) come back
    NOT_IN_NC too — callers decide whether to ask."""
    ids = list(dict.fromkeys(uuid.UUID(str(i)) for i in invoice_ids))
    out = {str(i): {"status": NOT_IN_NC, "bill_no": None, "payment_no": None,
                    "paid_on": None, "open_balance": None} for i in ids}
    if not ids:
        return out
    for r in (await db.execute(_SQL, {"ids": ids})).mappings():
        out[str(r["id"])] = {
            "status": _status(r["cr"], r["bal"], r["open_lines"], bool(r["pay_nos"])),
            "bill_no": r["bills"], "payment_no": r["pay_nos"],
            "paid_on": r["paid_on"].isoformat() if r["paid_on"] else None,
            "open_balance": str(r["bal"]),
        }
    return out


def rollup(statuses: list[str]) -> str | None:
    """One status for a PA from its invoices': every one settled → PAID (or
    CLOSED if none was by payment); none settled and all absent → NOT_IN_NC;
    all open → OPEN; anything in between → PARTIAL. None when no invoices."""
    if not statuses:
        return None
    if all(s in SETTLED for s in statuses):
        return PAID if PAID in statuses else CLOSED
    if all(s == NOT_IN_NC for s in statuses):
        return NOT_IN_NC
    if all(s in (OPEN, NOT_IN_NC) for s in statuses):
        return OPEN
    return PARTIAL
