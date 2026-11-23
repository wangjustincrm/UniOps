"""An order's Payment (user 2026-11-19 / 2026-11-23): beside Fulfillment and Invoice, whether the money came.

"When it is settled it means we received it; if there is a problem it is not part of this app." For now Settled is a
mark a finance user sets by hand (sales.settle) for every source — retailer, manual, Shopify — with the date the
money came and a reference. It does not change the order's state (Completed stays as it is: the user has not
decided whether it should wait for Settled).

What a list shows (the rule, as SQL, is Order.payment_status_sql in models/invoices.py; tests hold them together):
- Settled: a finance user's mark (manual) — it always wins; or, for a Shopify order, derived (auto, user 2026-11-23):
  every captured payment is a gateway transaction (Authorize.Net / PayPal, as Payments matched it) that is in a closed
  settlement (PayPal: transferred to the bank, not "In PayPal balance"); its date and reference are the latest such
  settlement's. Undoing the manual mark falls back to this;
- Not needed: cancelled and never invoiced, or historical (the history replay; uniops-3d 2026-11-23, easy to reverse);
- Not needed also: a Shopify order with nothing left to receive (net 0: fully refunded, or free);
- Waiting for the gateway: a Shopify order whose captured payments are all matched to gateway transactions, none yet in a
  closed settlement (PayPal: still in the PayPal balance). It settles by itself;
- partly settled, a payment not via our gateways (e.g. AlphaPay) or not matched yet: Not settled (no rule is guessed);
- Due / Overdue: invoiced (sent) and the terms say "Net N": due on the invoice date + N days. No terms, or terms in
  other words: plain Not settled (terms are never guessed);
- Not settled: everything else. A Draft has none.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

SETTLED = "Settled"
NOT_SETTLED = "Not settled"
DUE = "Due"
OVERDUE = "Overdue"
NOT_NEEDED = "Not needed"
WAITING = "Waiting for the gateway"
PAYMENT_STATUSES = (NOT_SETTLED, WAITING, DUE, OVERDUE, SETTLED, NOT_NEEDED)

# "Net 30", "NET30", " net 45 " — nothing else is read as terms. The same pattern runs in SQL (lower-cased text).
TERMS_SQL_PATTERN = r"^\s*net\s*(\d{1,3})\s*$"
_TERMS = re.compile(TERMS_SQL_PATTERN)


def terms_days(terms: str | None) -> int | None:
    """The days of "Net N" terms; None for no terms or any other wording."""
    m = _TERMS.match((terms or "").lower())
    return int(m.group(1)) if m else None


def payment_status(*, draft: bool, cancelled: bool, settled: bool, invoice_sent: bool, invoice_date: date | None,
                   terms: str | None, today: date, historical: bool = False, shopify: bool = False, net_zero: bool = False,
                   captured: int = 0, other: int = 0, matched: int = 0, closed: int = 0) -> str | None:
    """The rule in Python (what the SQL does, for tests and the help). `settled`: the manual mark. For a Shopify order:
    its successful captured payments (`captured`), how many are not via our gateways (`other`), matched to a gateway
    transaction (`matched`), and in a closed settlement (`closed`)."""
    if settled:
        return SETTLED
    if draft:
        return None
    if historical:
        return NOT_NEEDED
    if auto_settled(captured=captured, other=other, closed=closed):
        return SETTLED
    if cancelled and not invoice_sent:
        return NOT_NEEDED
    if shopify and net_zero:
        return NOT_NEEDED
    if captured and not other and matched == captured and closed == 0:
        return WAITING
    days = terms_days(terms)
    if invoice_sent and invoice_date is not None and days is not None:
        return OVERDUE if invoice_date + timedelta(days=days) < today else DUE
    return NOT_SETTLED


def auto_settled(*, captured: int, other: int, closed: int) -> bool:
    """A Shopify order the gateway settled: every captured payment is in a closed settlement, none elsewhere."""
    return captured > 0 and other == 0 and closed == captured
