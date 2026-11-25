"""What each order Status, On Hold reason and Detail means, in the words the user reads (the user,
2026-11-16: one source for help, kept next to the rules it explains). Keyed by the constants in
order_status.py; the texts name the labels users see ("To Fulfill", not WAITING_TO_SEND).

The front end shows these on the status badge (GET /help/texts). Meant to be the source the
AI guide layer reads for Sales too, when Sales joins it (not wired yet: epms-api/app/knowledge has no
Sales). `buttons` lists every button a text names: tests/test_status_help.py checks each is a
real label in the front end, and that every status and reason has a text.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.domain.order_payment import DUE, NOT_NEEDED, NOT_SETTLED, OVERDUE, SETTLED, WAITING
from app.domain.order_status import (
    ARCHIVED, CANCELLED, CANCELLED_AT_SOURCE, COMPLETED, CONFLICT_945, DELIVERY_DUE, DRAFT, FAILED_940, INVOICE_BLOCKED,
    INVOICE_FAILED, NEEDS_REVIEW, ON_HOLD, PAYMENT_REQUIRED, REJECTED_940, SENDS, SHOPIFY_FAILED, TIMEOUT_997, WAITING_945,
    WAITING_997, WAITING_DELIVERY, WAITING_INVOICE, WAITING_TO_SEND, DetailFacts, Display, StatusFacts,
)
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv

MAX_BODY = 320  # a popover, read on a phone: a few lines, never an article
SHORT_SHIPPED = "Short shipped"
OVER_SHIPPED = "Over shipped"
SHIPMENTS = "Shipments"  # the "2 shipments" mark


@dataclass(frozen=True)
class Help:
    body: str
    buttons: tuple[str, ...] = ()


STATUS_HELP: dict[str, Help] = {
    DRAFT: Help("A manual order not submitted yet. Nothing is sent until you Submit.", ("Submit",)),
    WAITING_TO_SEND: Help(
        "Waiting to go to the warehouse. \"Sends at …\" counts down the partner's delay (a Shopify order waits so the "
        "customer can still change it). \"This partner sends by hand\": use Send 940 now.", ("Send 940 now",)),
    WAITING_997: Help(
        "The 940 is with the warehouse; waiting for its 997 to accept it. To stop it now, ask the warehouse to cancel "
        "the 940: Cancel order opens once no 997 comes in time (997 Timeout). Don't Send 940 now meanwhile.",
        ("Cancel order", "Send 940 now")),
    WAITING_945: Help(
        "The warehouse accepted it and is picking; waiting for the 945. A Shopify order is already fulfilled and "
        "archived in Shopify; the 945 adds the tracking and tells the customer. \"Writing the tracking to Shopify\": "
        "the 945 arrived and the tracking is being written."),
    WAITING_DELIVERY: Help(
        "Invoiced partners only: the 945 arrived. Confirm delivery when it is due; that makes the invoice.",
        ("Confirm delivery",)),
    WAITING_INVOICE: Help("Delivery confirmed; the invoice is ready to send or queued."),
    COMPLETED: Help("Nothing left to do. A partner without invoicing comes here straight after the 945."),
    ON_HOLD: Help("Stopped until someone acts; the reason says why."),
    CANCELLED: Help("Cancelled; nothing more is sent."),
}

HOLD_HELP: dict[str, Help] = {
    NEEDS_REVIEW: Help("Something doesn't match the Item Master (an item, a unit, a partner code, a DC route). Fix it there, "
                       "then Re-check.", ("Re-check",)),
    PAYMENT_REQUIRED: Help("Shopify hasn't reported it paid. It goes on by itself once paid; nothing to do unless the "
                           "payment never comes."),
    ARCHIVED: Help("Archived in Shopify before it was sent. Un-archive it in Shopify to send it; leave it if the archive "
                   "was intended."),
    REJECTED_940: Help("The warehouse's 997 rejected the 940. Fix the cause, then Send 940 now.", ("Send 940 now",)),
    FAILED_940: Help("The 940 couldn't be sent (connection or file). Send 940 now once fixed.", ("Send 940 now",)),
    TIMEOUT_997: Help("No 997 from the warehouse in time. Check with the warehouse: Send 940 now sends it again; if "
                      "the order should not go (refunded, or the warehouse cancelled it), Cancel order with the reason.",
                      ("Send 940 now", "Cancel order")),
    CANCELLED_AT_SOURCE: Help(
        "Cancelled by the customer or in Shopify after the 940 reached the warehouse. Check with the warehouse, then "
        "Release hold: Cancel the order, Mark as shipped (if a 945 came), or Back to the status it held.",
        ("Release hold", "Cancel the order", "Mark as shipped", "Back to")),
    CONFLICT_945: Help("Two 945s for this order. Compare the 945s: add the second (Apply the missing part, or Apply, more than "
                       "ordered), Keep the applied 945, or Cancel the order. Its task says why it was not added.",
                       ("Compare the 945s", "Apply the missing part", "Apply, more than ordered", "Keep the applied 945",
                        "Cancel the order")),
    SHOPIFY_FAILED: Help("Writing to Shopify failed. Retry it on the shipment: Shipments & lots → Retry in Shopify.",
                         ("Retry in Shopify",)),
    INVOICE_FAILED: Help("Sending the invoice failed. Open the invoice; fix it with Edit invoice if needed, then Send.",
                         ("Edit invoice", "Send")),
    INVOICE_BLOCKED: Help("The invoice is on hold; its task says why. Open the invoice, Edit invoice if needed, then Release.",
                          ("Edit invoice", "Release")),
}

# Payment (app/domain/order_payment.py, user 2026-11-23): beside the Status; Settled is a finance user's mark.
PAYMENT_HELP: dict[str, Help] = {
    NOT_SETTLED: Help("Finance hasn't marked the money received yet. A finance user uses Mark settled with the date it came "
                      "and a reference (bank, cheque or remittance no.).", ("Mark settled",)),
    DUE: Help("Invoiced; the terms (Net N days from the invoice date) are not up yet. Mark settled when the money comes.",
              ("Mark settled",)),
    OVERDUE: Help("Invoiced and past its terms (the invoice date + Net N days). Follow up with the partner; Mark settled "
                  "when the money comes.", ("Mark settled",)),
    SETTLED: Help("The money came. Settled (auto): a Shopify order whose payments are in a gateway settlement (named on "
                  "the order). Otherwise Finance marked it, with the date and reference; Undo settled (with a reason) "
                  "undoes only that mark.", ("Undo settled",)),
    WAITING: Help("Shopify order: its payments are with the gateway (Authorize.Net, or in the PayPal balance) but in no "
                  "settlement yet. It becomes Settled (auto) by itself; nothing to do."),
    NOT_NEEDED: Help("No money is expected: cancelled and never invoiced, a Shopify order with nothing left to receive "
                     "(fully refunded or free), or an old (historical) order whose gateway does not say Settled."),
}

DETAIL_HELP: dict[str, Help] = {
    SENDS: Help("When the 940 goes to the warehouse by itself, after the partner's delay."),
    DELIVERY_DUE: Help("The requested delivery date (for DC-routed partners, the do-not-deliver-after date). Confirm "
                       "delivery once it has been delivered; past this date it shows as late.", ("Confirm delivery",)),
    SHORT_SHIPPED: Help("The 945 shipped less than ordered. Backordered = ordered − shipped (EA); no second 940 asks for the "
                        "rest. A later 945 of a Shopify order that brings it is added by itself."),
    OVER_SHIPPED: Help("The 945s shipped more than the 940 carried: how many EA more, over the order's lines."),
    SHIPMENTS: Help("How many 945s of this order count: what they shipped is added up. A resend, or one that was kept and not "
                    "applied, is not counted."),
}


def payload() -> dict[str, dict[str, dict[str, str]]]:
    """GET /help/texts: {statuses | holds | details | payments | terms: {label: {body}}} (terms: glossary.py)."""
    from app.domain.glossary import TERMS  # glossary imports Help from here

    def part(d: dict[str, Help]) -> dict[str, dict[str, str]]:
        return {k: {"body": h.body} for k, h in d.items()}
    return {"statuses": part(STATUS_HELP), "holds": part(HOLD_HELP), "details": part(DETAIL_HELP), "payments": part(PAYMENT_HELP),
            "terms": part(TERMS)}


# ── the next step of one order (the user, 2026-11-16: approved table) ─────────────────────────────
# Under the Status on the order: whose turn it is and what to do, from this order's own facts. The
# dates are on the Detail line beside it, so these texts carry none.
YOU, WAREHOUSE, SHOPIFY, NOBODY = "you", "warehouse", "shopify", "nobody"


@dataclass(frozen=True)
class Step:
    who: str
    text: str
    buttons: tuple[str, ...] = ()


STEPS: dict[str, Step] = {
    "draft": Step(YOU, "Check it, then Submit.", ("Submit",)),
    "send.delay": Step(NOBODY, "It goes to the warehouse by itself when the delay ends."),
    "send.manual": Step(YOU, "Send 940 now when it should go.", ("Send 940 now",)),
    "send.next_run": Step(NOBODY, "It goes to the warehouse on the next run."),
    "sent": Step(WAREHOUSE, "Waiting for the warehouse's 997."),
    "fulfilling": Step(WAREHOUSE, "Waiting for the warehouse's 945."),
    "fulfilling.tracking": Step(NOBODY, "The tracking is being written to Shopify."),
    "send.runs_off": Step(YOU, "Automatic runs are off: Send 940 now, or turn them on in Connections.", ("Send 940 now",)),
    "send.made_runs_off": Step(YOU, "Automatic runs are off: the 940 is made and goes out once they are turned on in Connections."),
    "shipped": Step(YOU, "Confirm delivery once it has been delivered.", ("Confirm delivery",)),
    "shipped.late": Step(YOU, "Delivery was due: confirm it if it has been delivered."),
    "shipped.awaiting": Step(NOBODY, "Nothing to do until the requested delivery date; then confirm delivery."),
    "shipped.pending": Step(NOBODY, "Delivery confirmation opens on the next run."),
    "shipped.runs_off": Step(YOU, "Automatic runs are off: delivery confirmation opens once they are turned on in Connections."),
    "invoice.ready": Step(YOU, "Open the invoice and Send it.", ("Send",)),
    "invoice.queued": Step(NOBODY, "The invoice goes out on the next run."),
    "invoice.runs_off": Step(YOU, "Automatic runs are off: the invoice goes out once they are turned on in Connections."),
}
HOLD_STEPS: dict[str, Step] = {
    NEEDS_REVIEW: Step(YOU, "Fix it in the Item Master, then Re-check.", ("Re-check",)),
    PAYMENT_REQUIRED: Step(SHOPIFY, "Nothing to do until Shopify reports it paid."),
    ARCHIVED: Step(YOU, "Un-archive it in Shopify to send it, or leave it."),
    REJECTED_940: Step(YOU, "Fix the cause shown, then Send 940 now.", ("Send 940 now",)),
    FAILED_940: Step(YOU, "Send 940 now once the connection works.", ("Send 940 now",)),
    TIMEOUT_997: Step(YOU, "Check with the warehouse, then Send 940 now, or Cancel order if it should not go.",
                      ("Send 940 now", "Cancel order")),
    CANCELLED_AT_SOURCE: Step(YOU, "Check with the warehouse, then Release hold.", ("Release hold",)),
    CONFLICT_945: Step(YOU, "Compare the 945s and choose.", ("Compare the 945s",)),
    SHOPIFY_FAILED: Step(YOU, "Retry in Shopify on the shipment.", ("Retry in Shopify",)),
    INVOICE_FAILED: Step(YOU, "Open the invoice, fix it if needed, then Send.", ("Send",)),
    INVOICE_BLOCKED: Step(YOU, "Open the invoice: its task says why; then Release.", ("Release",)),
}


def next_step_key(d: Display, f: StatusFacts, n: DetailFacts, today: date, *, runs_on: bool = True) -> str | None:
    """Which step applies to this order (a STEPS key, or the hold reason); None when nothing is left
    (Completed, Cancelled) or for the test run. `today` is Toronto's (invoicing.today); `runs_on`:
    automatic runs happen (the 940s, the invoice's advance and the queued invoices all run in the
    system job, under the master switch)."""
    s = d.order_status
    if s == ON_HOLD:
        return d.on_hold_reason if d.on_hold_reason in HOLD_STEPS else None
    if f.shadow:  # the test run: Zoho sends and fulfils it, so no step here is true for it
        return None
    if s == DRAFT:
        return "draft"
    if s == WAITING_TO_SEND:
        if f.fulfillment == F.PENDING and n.manual_sending:
            return "send.manual"
        if not runs_on:  # a made 940 (GENERATED) can't be sent by hand: it waits for the runs
            return "send.runs_off" if f.fulfillment == F.PENDING else "send.made_runs_off"
        return "send.delay" if f.fulfillment == F.PENDING and n.send_at and not f.delay_over else "send.next_run"
    if s == WAITING_997:
        return "sent"
    if s == WAITING_945:
        return "fulfilling.tracking" if f.fulfillment == F.SHIPPED else "fulfilling"
    if s == WAITING_DELIVERY:  # only this invoice state takes Confirm delivery (invoicing.confirm)
        if f.invoice == Inv.WAITING_FOR_DELIVERY_CONFIRMATION.value:
            return "shipped.late" if n.delivery_due and n.delivery_due < today else "shipped"
        if f.invoice == Inv.AWAITING_REQUESTED_DATE.value:
            return "shipped.awaiting"
        return "shipped.pending" if runs_on else "shipped.runs_off"
    if s == WAITING_INVOICE:
        if f.invoice == Inv.QUEUED.value:
            return "invoice.queued" if runs_on else "invoice.runs_off"
        return "invoice.ready"
    return None


def next_step(d: Display, f: StatusFacts, n: DetailFacts, today: date, *, runs_on: bool = True) -> Step | None:
    key = next_step_key(d, f, n, today, runs_on=runs_on)
    return None if key is None else HOLD_STEPS.get(key) or STEPS[key]
