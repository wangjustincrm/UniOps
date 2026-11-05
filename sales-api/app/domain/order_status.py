"""What people see of an order (PRD 9.1, FR-OB-52): one Status, the reason of On
Hold, the Short shipped mark and the Detail. Derived, never stored. The
state machines in app/domain/status.py stay the source: nothing here sends,
blocks or decides anything (`order_rules.order_state` still serves eligibility).

Each rule is written once, as data over an algebra: `status_rules(f, b)` gives
[(condition, value)] and the first true condition wins. Run with `PY` over
`StatusFacts` it is `display()`, for a row; run with the SQL algebra over columns
(app/services/order_status_sql.py) it is the CASE every list filter, sort, group
and Home / Health count uses. So no list has a condition of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv

DRAFT, CANCELLED, ON_HOLD, COMPLETED = "Draft", "Cancelled", "On Hold", "Completed"
WAITING_TO_SEND, WAITING_997, WAITING_945 = "Waiting to Send", "Waiting for 997", "Waiting for Packing List"
WAITING_DELIVERY, WAITING_INVOICE = "Waiting for Delivery", "Waiting for Invoice"
WITH_ZOHO, COMPLETED_SHADOW, DELIVERY_SHADOW = "With Zoho (shadow)", "Completed (shadow)", "Waiting for Delivery (shadow)"
ORDER_STATUSES = (DRAFT, WAITING_TO_SEND, WAITING_997, WAITING_945, WAITING_DELIVERY, WAITING_INVOICE, COMPLETED, ON_HOLD,
                  CANCELLED, WITH_ZOHO, DELIVERY_SHADOW, COMPLETED_SHADOW)
WAREHOUSE_STATUSES = (WAITING_TO_SEND, WAITING_997, WAITING_945)
INVOICE_WORK_STATUSES = (WAITING_DELIVERY, WAITING_INVOICE)
ACTIVE_STATUSES = (*WAREHOUSE_STATUSES, *INVOICE_WORK_STATUSES, WITH_ZOHO, DELIVERY_SHADOW)
COMPLETED_STATUSES = (COMPLETED, COMPLETED_SHADOW)
NOT_OPEN_STATUSES = (DRAFT, *COMPLETED_STATUSES, CANCELLED)  # the Open view is everything else, On Hold included

NEEDS_REVIEW, PAYMENT_REQUIRED, ARCHIVED = "Needs Review", "Payment Required", "Archived in Shopify"
REJECTED_940, FAILED_940, TIMEOUT_997 = "940 Rejected", "940 Failed", "997 Timeout"
CANCELLED_AT_SOURCE, CONFLICT_945 = "Cancelled at Source", "945 Conflict"
SHOPIFY_FAILED, INVOICE_FAILED, INVOICE_BLOCKED = "Shopify Update Failed", "Invoice Failed", "Invoice Blocked"
HOLD_REASONS = (NEEDS_REVIEW, PAYMENT_REQUIRED, ARCHIVED, REJECTED_940, FAILED_940, TIMEOUT_997, CANCELLED_AT_SOURCE,
                CONFLICT_945, SHOPIFY_FAILED, INVOICE_FAILED, INVOICE_BLOCKED)

SHOPIFY = "SHOPIFY"


@dataclass(frozen=True)
class StatusFacts:
    """One order, as the rules read it. Every fact is a plain value, never NULL
    in SQL either (three-valued logic would make the two runs differ)."""
    fulfillment: str
    invoice: str = Inv.PENDING.value
    source_type: str = "MANUAL"
    shadow: bool = False
    archived: bool = False                # archived in Shopify
    fulfilled_in_shopify: bool = False
    cancelled_at_source: bool = False
    has_945: bool = False                 # a 945 is linked, applied or not
    shopify_failed: bool = False          # a shipment's write failed or was refused
    shopify_pending: bool = False         # a shipment's write is queued
    invoicing: bool = False               # the partner's invoice method is not None
    short: bool = False                   # the 945 (applied or the shadow run's) left something unshipped


class Python:
    """The algebra over plain values."""
    @staticmethod
    def is_(value: Any, *values: Any) -> bool:
        return value in values

    @staticmethod
    def all_(*conditions: bool) -> bool:
        return all(conditions)

    @staticmethod
    def any_(*conditions: bool) -> bool:
        return any(conditions)

    @staticmethod
    def not_(condition: bool) -> bool:
        return not condition


PY = Python()
Rules = list[tuple[Any, str]]


def _zoho_took_it(f, b):
    """Zoho has the order: a 945 came, or Shopify says fulfilled. Not proof of shipment: Zoho
    fulfills and archives before the goods leave (dev2: 36 of 36 orders, 2.5 to 121.7 hours
    before the 945). Only `has_945` proves shipped."""
    return b.any_(f.has_945, f.fulfilled_in_shopify)


def hold_rules(f, b) -> Rules:
    """D17, with its reason. A draft or a cancelled order holds nothing. During the
    shadow run a question before the 940 is moot once Zoho has taken the order."""
    live = b.not_(b.is_(f.fulfillment, F.DRAFT.value, F.CANCELLED.value))
    moot = b.all_(f.shadow, _zoho_took_it(f, b))
    open_question = b.not_(moot)
    archived = b.any_(
        b.all_(b.is_(f.fulfillment, F.ARCHIVE_SUPPRESSED.value), open_question),
        b.all_(b.is_(f.fulfillment, F.PENDING.value), f.archived, open_question))  # re-check left it Pending (data before the fix)
    on_hold = b.is_(f.fulfillment, F.ON_HOLD.value)
    return [
        (b.all_(b.is_(f.fulfillment, F.REVIEW.value), open_question), NEEDS_REVIEW),
        (b.all_(b.is_(f.fulfillment, F.PAYMENT_HOLD.value), open_question), PAYMENT_REQUIRED),
        (archived, ARCHIVED),
        (b.is_(f.fulfillment, F.REJECTED_940.value), REJECTED_940),
        (b.is_(f.fulfillment, F.FAILED_940.value), FAILED_940),
        (b.is_(f.fulfillment, F.TIMEOUT_940.value), TIMEOUT_997),
        (b.all_(on_hold, f.cancelled_at_source), CANCELLED_AT_SOURCE),
        (on_hold, CONFLICT_945),  # the only other way into On Hold (FR-OB-18, FR-OB-25)
        (b.all_(live, f.shopify_failed), SHOPIFY_FAILED),
        (b.all_(live, b.is_(f.invoice, Inv.FAILED.value)), INVOICE_FAILED),
        (b.all_(live, b.is_(f.invoice, Inv.BLOCKED.value)), INVOICE_BLOCKED),
    ]


def status_rules(f, b) -> Rules:
    """The first true rule is the Order status; the last has a true condition."""
    is_f = lambda *v: b.is_(f.fulfillment, *(s.value for s in v))  # noqa: E731
    is_inv = lambda *v: b.is_(f.invoice, *(s.value for s in v))  # noqa: E731
    shipped = is_f(F.SHIPPED)
    return [
        (is_f(F.CANCELLED), CANCELLED),
        (is_f(F.DRAFT), DRAFT),
        (b.any_(*(c for c, _ in hold_rules(f, b))), ON_HOLD),
        (b.all_(f.shadow, f.has_945, b.is_(f.source_type, SHOPIFY)), COMPLETED_SHADOW),
        (b.all_(f.shadow, f.has_945, f.invoicing), DELIVERY_SHADOW),
        (b.all_(f.shadow, f.has_945), COMPLETED_SHADOW),
        (f.shadow, WITH_ZOHO),
        (is_f(F.PENDING, F.GENERATED_940), WAITING_TO_SEND),
        (is_f(F.SENT_940), WAITING_997),
        (is_f(F.ACCEPTED_940), WAITING_945),
        # Shipped is final, short or not (D18). The tracking is still on its way to Shopify:
        # not Completed yet, and there is no later stage to name it.
        (b.all_(shipped, f.shopify_pending), WAITING_945),
        (b.all_(shipped, is_inv(Inv.READY, Inv.QUEUED)), WAITING_INVOICE),
        (b.all_(shipped, is_inv(Inv.PENDING, Inv.AWAITING_REQUESTED_DATE, Inv.WAITING_FOR_DELIVERY_CONFIRMATION)), WAITING_DELIVERY),
        (shipped, COMPLETED),
    ]


def _first(rules: Rules, otherwise: str | None) -> str | None:
    return next((value for condition, value in rules if condition), otherwise)


@dataclass(frozen=True)
class Display:
    order_status: str
    on_hold_reason: str | None
    short_shipped: bool  # a mark beside the Status, not a Status: a short shipment is final (D18)

    @property
    def label(self) -> str:
        """As shown: "On Hold: 997 Timeout"."""
        return f"{self.order_status}: {self.on_hold_reason}" if self.on_hold_reason else self.order_status


def display(f: StatusFacts) -> Display:
    status = _first(status_rules(f, PY), WAITING_TO_SEND)
    reason = _first(hold_rules(f, PY), None) if status == ON_HOLD else None
    return Display(status, reason, f.short)


@dataclass(frozen=True)
class DetailFacts:
    """What the Detail can name, beyond the status."""
    manual_sending: bool = False
    send_at: datetime | None = None         # the delay's end
    sent_at: datetime | None = None         # the 940's
    requested_date: date | None = None
    review_reason: str | None = None
    archived_at: datetime | None = None
    cancelled_at: datetime | None = None
    file_note: str | None = None            # the 940's rejection or failure
    shopify_error: str | None = None
    invoice_error: str | None = None


def _line(text: str | None) -> str | None:
    return (text or "").strip().split("\n")[0][:160] or None


def status_detail(d: Display, f: StatusFacts, n: DetailFacts) -> tuple[str | None, datetime | date | None]:
    """Only what the Status does not already say, and the time it names (shown beside it
    in the viewer's format); nothing otherwise. Never an instruction."""
    s, reason = d.order_status, d.on_hold_reason
    if s == ON_HOLD:
        return {
            NEEDS_REVIEW: (_line(n.review_reason), None),
            ARCHIVED: ("Archived", n.archived_at) if n.archived_at else (None, None),
            CANCELLED_AT_SOURCE: ("Cancelled", n.cancelled_at) if n.cancelled_at else (None, None),
            REJECTED_940: (_line(n.file_note), None),
            FAILED_940: (_line(n.file_note), None),
            TIMEOUT_997: ("No 997 since", n.sent_at) if n.sent_at else (None, None),
            SHOPIFY_FAILED: (_line(n.shopify_error), None),
            INVOICE_FAILED: (_line(n.invoice_error), None),
        }.get(reason, (None, None))
    if s == WAITING_TO_SEND and f.fulfillment == F.PENDING:
        if n.manual_sending:
            return "This partner sends by hand", None
        return ("Sends at", n.send_at) if n.send_at else (None, None)
    if s == WAITING_997 or (s == WAITING_945 and f.fulfillment != F.SHIPPED):
        return ("Since", n.sent_at) if n.sent_at else (None, None)
    if s == WAITING_945:
        return "Writing the tracking to Shopify", None
    if s == WAITING_DELIVERY and f.invoice == Inv.AWAITING_REQUESTED_DATE and n.requested_date:
        return "Requested date", n.requested_date
    return None, None


def send_time(*, from_receipt: bool, received_at: datetime, paid_at: datetime | None, delay_minutes: int | None) -> datetime | None:
    """D20: when the delay ends (an INVOICE partner's from receipt, a PREPAID one's from payment)."""
    start = received_at if from_receipt else paid_at
    return None if start is None or delay_minutes is None else start + timedelta(minutes=delay_minutes)
