"""Closed value sets and pure rules of the payment gateways' data (Finance, user 2026-11-17).

The Authorize.Net transaction statuses (Transaction Details API) say whether money moved, and in
which direction; `kind_of` / `settled_of` turn them into the one shape every gateway shares.
"""
from __future__ import annotations

GATEWAYS = ("AUTHORIZE_NET", "PAYPAL")
LABELS = {"AUTHORIZE_NET": "Authorize.Net", "PAYPAL": "PayPal"}  # as Finance says it (the Method column)
# Shopify's names of the gateways we read (OrderPayment.gateway, lower-cased); any other (AlphaPay, a gift card…) is not ours.
SHOPIFY_GATEWAYS = ("authorize.net", "paypal")
# The PayPal "settlement" of what has not been transferred to the bank yet: the money is not settled (services/paypal_settlements.py).
PAYPAL_IN_BALANCE = "In PayPal balance"
TX_KINDS = ("sale", "refund", "void", "other")
# How a gateway transaction was found to be a Shopify payment, strongest first. AMOUNT_TIME is a
# probable match only (same amount, same kind, within minutes, the only candidate).
MATCH_METHODS = ("TRANSACTION_ID", "SHOPIFY_PAYMENT_ID", "ORDER_NUMBER", "AMOUNT_TIME")
PROBABLE = frozenset({"AMOUNT_TIME"})

# Authorize.Net: money taken from the card / given back, once settled.
ANET_SETTLED_SALE = frozenset({"settledSuccessfully"})
ANET_SETTLED_REFUND = frozenset({"refundSettledSuccessfully"})
ANET_REFUND = frozenset({"refundSettledSuccessfully", "refundPendingSettlement"})
ANET_VOID = frozenset({"voided"})
ANET_SALE = frozenset({"settledSuccessfully", "capturedPendingSettlement", "authorizedPendingCapture",
                       "declined", "expired", "FDSPendingReview", "FDSAuthorizedPendingReview", "underReview",
                       "approvedReview", "failedReview", "couldNotVoid", "generalError", "settlementError",
                       "communicationError"})


def kind_of(status: str | None) -> str:
    s = status or ""
    if s in ANET_REFUND:
        return "refund"
    if s in ANET_VOID:
        return "void"
    if s in ANET_SALE:
        return "sale"
    return "other"  # chargeback, returnedItem, …: shown, never matched to a Shopify payment


def settled_of(status: str | None) -> bool:
    """Money moved in the batch: a settled sale or a settled refund."""
    return (status or "") in ANET_SETTLED_SALE | ANET_SETTLED_REFUND


# PayPal (user 2026-11-19): a transaction's event code says what it is (T00xx a payment, T11xx a reversal,
# T04xx a transfer to the bank, T02xx a currency conversion, T01xx a fee, T21xx a hold …), its amount's sign
# which way the money went, and its status (S success, P pending, V reversed, F partially refunded, D denied)
# whether it moved.
PAYPAL_REFUND = frozenset({"T1107"})                       # a refund of a payment received
PAYPAL_SETTLED = frozenset({"S", "F"})                     # F: a payment later partly refunded still came in


def paypal_kind(event_code: str | None, value: object) -> str:
    """sale: a payment received (T00xx, money in); refund: a refund of one (T1107); everything else (a fee, a
    conversion between currencies, a transfer to the bank, a chargeback, a hold, a payment sent) is other:
    shown, never counted as a sale or a refund, never matched to Shopify."""
    code = (event_code or "").upper()
    try:
        positive = float(str(value)) > 0
    except (TypeError, ValueError):
        positive = False
    if code.startswith("T00") and positive:
        return "sale"
    if code in PAYPAL_REFUND:
        return "refund"
    return "other"


def paypal_settled(status: str | None, kind: str) -> bool:
    """Money moved: a sale or refund PayPal marks done (not pending, reversed or denied)."""
    return kind in ("sale", "refund") and (status or "").upper() in PAYPAL_SETTLED


def paypal_withdrawal(event_code: str | None) -> bool:
    """A transfer of the balance to the bank (T04xx): where a PayPal settlement ends."""
    return (event_code or "").upper().startswith("T04")
