"""Closed value sets and pure rules of the payment gateways' data (Finance, user 2026-11-17).

The Authorize.Net transaction statuses (Transaction Details API) say whether money moved, and in
which direction; `kind_of` / `settled_of` turn them into the one shape every gateway shares.
"""
from __future__ import annotations

GATEWAYS = ("AUTHORIZE_NET", "PAYPAL")
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
