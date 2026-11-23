"""An order's Payment (user 2026-11-19 / 2026-11-23): beside Fulfillment and Invoice, whether the money came.

"When it is settled it means we received it; if there is a problem it is not part of this app." For now Settled is a
mark a finance user sets by hand (sales.settle) for every source — retailer, manual, Shopify — with the date the
money came and a reference. It does not change the order's state (Completed stays as it is: the user has not
decided whether it should wait for Settled).

What a list shows (the rule, as SQL, is Order.payment_status_sql in models/invoices.py; tests hold them together):
- Settled: the mark is set (a later derived source, e.g. Shopify from the gateway settlements, goes beside it; the
  manual mark wins);
- Not needed: cancelled and never invoiced, or historical (the history replay; uniops-3d 2026-11-23, easy to reverse);
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
PAYMENT_STATUSES = (NOT_SETTLED, DUE, OVERDUE, SETTLED, NOT_NEEDED)

# "Net 30", "NET30", " net 45 " — nothing else is read as terms. The same pattern runs in SQL (lower-cased text).
TERMS_SQL_PATTERN = r"^\s*net\s*(\d{1,3})\s*$"
_TERMS = re.compile(TERMS_SQL_PATTERN)


def terms_days(terms: str | None) -> int | None:
    """The days of "Net N" terms; None for no terms or any other wording."""
    m = _TERMS.match((terms or "").lower())
    return int(m.group(1)) if m else None


def payment_status(*, draft: bool, cancelled: bool, settled: bool, invoice_sent: bool, invoice_date: date | None,
                   terms: str | None, today: date, historical: bool = False) -> str | None:
    """The rule in Python (what the SQL does, for tests and the help)."""
    if settled:
        return SETTLED
    if draft:
        return None
    if historical:
        return NOT_NEEDED
    if cancelled and not invoice_sent:
        return NOT_NEEDED
    days = terms_days(terms)
    if invoice_sent and invoice_date is not None and days is not None:
        return OVERDUE if invoice_date + timedelta(days=days) < today else DUE
    return NOT_SETTLED
