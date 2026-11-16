"""What each order Status, On Hold reason and Detail means, in the words the user reads (the user,
2026-11-16: one source for help, kept next to the rules it explains). Keyed by the constants in
order_status.py; the texts name the labels users see ("To Fulfill", not WAITING_TO_SEND).

The front end shows these on the status badge (GET /help/order-statuses); the AI guide layer reads
the same dict. `buttons` lists every button a text names: tests/test_status_help.py checks each is a
real label in the front end, and that every status and reason has a text.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.order_status import (
    ARCHIVED, CANCELLED, CANCELLED_AT_SOURCE, COMPLETED, CONFLICT_945, DELIVERY_DUE, DRAFT, FAILED_940, INVOICE_BLOCKED,
    INVOICE_FAILED, NEEDS_REVIEW, ON_HOLD, PAYMENT_REQUIRED, REJECTED_940, SENDS, SHOPIFY_FAILED, TIMEOUT_997, WAITING_945,
    WAITING_997, WAITING_DELIVERY, WAITING_INVOICE, WAITING_TO_SEND,
)

MAX_BODY = 320  # a popover, read on a phone: a few lines, never an article
SHORT_SHIPPED = "Short shipped"


@dataclass(frozen=True)
class Help:
    body: str
    buttons: tuple[str, ...] = ()


STATUS_HELP: dict[str, Help] = {
    DRAFT: Help("A manual order not submitted yet. Nothing is sent until you Submit.", ("Submit",)),
    WAITING_TO_SEND: Help(
        "Waiting to go to the warehouse. \"Sends at …\" counts down the partner's delay (a Shopify order waits so the "
        "customer can still change it). \"This partner sends by hand\": use Send 940 now.", ("Send 940 now",)),
    WAITING_997: Help("The 940 is with the warehouse; waiting for its 997 to accept it."),
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
    TIMEOUT_997: Help("No 997 from the warehouse in time. Check with the warehouse; Send 940 now sends it again.",
                      ("Send 940 now",)),
    CANCELLED_AT_SOURCE: Help(
        "Cancelled by the customer or in Shopify after the 940 reached the warehouse. Check with the warehouse, then "
        "Release hold: Cancel the order, Mark as shipped, or go back.", ("Release hold", "Cancel the order", "Mark as shipped")),
    CONFLICT_945: Help("Two 945s for this order. Compare the 945s: Keep the applied 945, or Cancel the order.",
                       ("Compare the 945s", "Keep the applied 945", "Cancel the order")),
    SHOPIFY_FAILED: Help("Writing to Shopify failed. Retry it on the shipment: Shipments & lots → Retry in Shopify.",
                         ("Retry in Shopify",)),
    INVOICE_FAILED: Help("Sending the invoice failed. Open the invoice; fix it with Edit invoice if needed, then Send.",
                         ("Edit invoice", "Send")),
    INVOICE_BLOCKED: Help("The invoice is on hold; its task says why. Open the invoice, Edit invoice if needed, then Release.",
                          ("Edit invoice", "Release")),
}

DETAIL_HELP: dict[str, Help] = {
    SENDS: Help("When the 940 goes to the warehouse by itself, after the partner's delay."),
    DELIVERY_DUE: Help("The requested delivery date (for DC-routed partners, the do-not-deliver-after date). Confirm "
                       "delivery once it has been delivered; past this date it shows as late.", ("Confirm delivery",)),
    SHORT_SHIPPED: Help("The 945 shipped less than ordered. Backordered = ordered − shipped (EA); the rest is not sent "
                        "later. Refund or adjust the short lines (in Shopify for a Shopify order)."),
}


def payload() -> dict[str, dict[str, dict[str, str]]]:
    """GET /help/order-statuses: {statuses | holds | details: {label: {body}}}."""
    def part(d: dict[str, Help]) -> dict[str, dict[str, str]]:
        return {k: {"body": h.body} for k, h in d.items()}
    return {"statuses": part(STATUS_HELP), "holds": part(HOLD_HELP), "details": part(DETAIL_HELP)}
