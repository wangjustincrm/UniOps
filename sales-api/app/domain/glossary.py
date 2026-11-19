"""What the module's words and figures mean (the user, 2026-11-16: approved glossary, round 2), keyed by
the label users read. Beside status_help.py and served with it (GET /help/texts); the screens attach a
term to a column, a field or a tile by `term:<key>`. tests/test_status_help.py checks every term a
screen names is here.

"In process" carries two meanings (an item's and a lot's): the user kept the label (option A) and the
text says both. SSCC: the pallet's (the 945 parser reads no case level).
"""
from __future__ import annotations

from app.domain.status_help import Help

TERMS: dict[str, Help] = {
    # EDI documents
    "850": Help("A retailer's purchase order, received through TrueCommerce. It becomes an order here."),
    "940": Help("Our shipping order to the warehouse (Radiant): what to pick and where to send it."),
    "997": Help("The warehouse's receipt for a 940 or a 943: accepted or rejected."),
    "945": Help("The warehouse's shipping report (the packing list): what left, which lots, pallets and tracking."),
    "943": Help("Our notice to the warehouse that stock is on its way to it (a transfer)."),
    "944": Help("The warehouse's receipt of a transfer, or of goods a customer sent back (an RA 944)."),
    "810": Help("The invoice sent to a retailer through TrueCommerce."),
    # an order
    "Order ref": Help("The partner's short code, \"-\", and its order number (a manual order: Sales' internal number). "
                      "Set when the order is made; never changes."),
    "Internal no.": Help("Sales' own running number for the order."),
    "Customer PO / reference": Help("The customer's own number for the order."),
    "Reason": Help("Why a manual order was made: Gift, Replacement, Sample, Staff purchase, Phone order, Other (the list is "
                   "set under Connections → settings). Chosen before it is submitted; Gift, Replacement and Sample are "
                   "Orders → Free goods."),
    "EA": Help("Each: the unit everything is counted in. Every other unit (CS…) has its EA factor per item in the "
               "Item Master."),
    "Delay": Help("How long a partner's orders wait before the 940 goes: from receipt (invoiced partners) or from "
                  "payment (prepaid). No delay: on the next run."),
    "Invoice price": Help("The invoice uses the PO price unless you set an invoice price: on the order (Edit invoice "
                          "prices) before the invoice is drafted, then on the invoice (Edit invoice).",
                          ("Edit invoice prices", "Edit invoice")),
    "SSCC": Help("The pallet's 18-digit label, from the 945."),
    "Lot / Expiry": Help("The production lot and its expiry date, from the 945."),
    "Backordered": Help("Ordered − shipped (EA). The rest is not sent later (one 945 per order)."),
    # inventory
    "On hand": Help("At the warehouse now: received + returned − shipped ± adjustments."),
    "On hold": Help("Held by the warehouse, as its latest stock report says."),
    "In process": Help("By item: EA on 940s that went out with no 945 yet. By lot: what the warehouse's latest report "
                       "shows picked for orders not shipped yet."),
    "Available": Help("On hand − on hold − in process."),
    "In transit to Radiant": Help("Sent on 943s and not received yet. Below zero: received but never sent (check it)."),
    "Returns on the way": Help("Sent back by customers, not at the warehouse yet. Below zero: received with no return "
                               "recorded."),
    "In transit to customers": Help("Shipped on a 945, delivery not final yet. It leaves this figure once the invoice is "
                                    "queued or sent, or straight away for an order with no invoice."),
    "Report difference": Help("Sales − the warehouse, at its latest applied stock report."),
    # payments
    "Sales, Refunds, Net": Help("Successful transactions only: payments, refunds, and payments − refunds."),
    "Gateway transaction ID": Help("The payment provider's own ID. Authorize.Net payments since Sep 18, 2025 have none in "
                                   "Shopify: it comes from Authorize.Net once the payment is matched there."),
    # gateways (Finance, 2026-11-17)
    "Settlement": Help("A deposit the payment provider paid to the bank: for Authorize.Net, one settled batch, about one a "
                       "day; for PayPal, one transfer to the bank, with everything since the previous one (what has not "
                       "been transferred yet is the In PayPal balance row). The payments in it are the money in that deposit."),
    "Matched by": Help("How the provider's transaction was found to be this Shopify payment: its transaction ID, the "
                       "Shopify payment ID, the order number, or only the same amount and time (probable: check it)."),
    "Difference": Help("The deposit's sales − refunds − the Shopify payments matched to it (fees aside). Not zero: some "
                       "money in the deposit is no Shopify payment, or a transaction is not matched yet."),
    # PayPal (Finance, 2026-11-19)
    "Carried balance": Help("PayPal: what went to the bank − the period's net after fees. Not zero when the transfer took "
                            "only part of the balance (or more, from before), a hold kept money back, or a deposit from the "
                            "bank funded refunds: Other events says which."),
}
