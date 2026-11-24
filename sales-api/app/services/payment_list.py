"""The Payments list (Finance, user 2026-11-16): one list, its uses as system views —
no page per method. Read-only; sales.read to look."""
from __future__ import annotations

from app.models.payment_list import INFO_ISSUES, ISSUES, KINDS, MATCHES, METHODS, SOURCES, STATUSES, PaymentRow
from app.platform.repository import Repository
from app.platform.resource import Resource
from app.platform.services import ReadOnlyService
from app.platform.views import Preset
from app.schemas.payment_list import PaymentRowRead

READ = "sales.read"
_SUCCESS = {"field": "status", "op": "eq", "value": "success"}
_NEWEST = [{"field": "processed_at", "desc": True}]


def _by_settlement(key: str, method: str) -> Preset:
    """A gateway's payments by the deposit they were paid out in (an Authorize.Net batch, a PayPal transfer)."""
    return Preset(key, f"{method} — by settlement", {
        "filters": [{"field": "method", "op": "eq", "value": method}, {"field": "settlement_id", "op": "not_null", "value": None}],
        "group": [{"field": "settled_date", "desc": True}, {"field": "settlement_id", "desc": True}], "sort": _NEWEST})


class PaymentRowRepository(Repository[PaymentRow]):
    model = PaymentRow
    search_fields = ("order_ref", "customer", "gateway_ref", "shopify_payment_id")
    suggestable = ("order_ref",)  # never a personal field (tests/test_suggestions.py)
    list_fields = ("id", "order_id", "order_ref", "customer", "processed_at", "processed_week", "processed_month", "method", "gateway",
                   "kind", "status", "amount", "currency", "sales_amount", "refund_amount", "net_amount", "net_after_fee", "gateway_ref",
                   "shopify_payment_id", "order_source", "financial_status", "order_cancelled", "issue", "discount", "shipping",
                   "order_total", "test", "gateway_status", "card_type", "match_method", "gateway_fee", "settlement_id",
                   "settlement_ref", "settled_at", "settled_date", "voided")
    labels = {"order_ref": "Order ref", "processed_at": "Processed", "processed_week": "Week", "processed_month": "Month",
              "method": "Method", "gateway": "Shopify gateway", "kind": "Kind", "sales_amount": "Sales", "refund_amount": "Refunds",
              "net_amount": "Net", "net_after_fee": "Net after fee", "gateway_fee": "Gateway fee", "gateway_ref": "Gateway transaction ID", "shopify_payment_id": "Shopify payment ID",
              "order_source": "Order source", "financial_status": "Order financial status", "order_cancelled": "Order cancelled",
              "issue": "Issue", "order_total": "Order total", "gateway_status": "Gateway status", "card_type": "Card type",
              "match_method": "Matched by", "settlement_id": "Settlement no.", "settlement_ref": "Batch / transfer ID", "settled_at": "Settled",
              "settled_date": "Settlement date", "voided": "Voided in the gateway"}
    enums = {"method": METHODS, "kind": KINDS, "status": STATUSES, "order_source": SOURCES, "issue": ISSUES, "match_method": MATCHES,
             "financial_status": ("paid", "partially_paid", "partially_refunded", "refunded", "pending", "authorized", "voided")}
    default_sort = ("-processed_at", "-id")
    groupable = ("method", "processed_week", "processed_month", "kind", "status", "order_source", "issue", "currency",
                 "settlement_id", "settlement_ref", "settled_date", "match_method", "card_type")
    summable = ("sales_amount", "refund_amount", "net_amount", "gateway_fee", "net_after_fee")
    sum_currency = "currency"
    money_fields = ("amount", "sales_amount", "refund_amount", "net_amount", "gateway_fee", "net_after_fee", "discount", "shipping", "order_total")
    # Amount: a refund is not negative here, so its sum mixes signs; the order's values repeat on each payment.
    unsummed = ("amount", "discount", "shipping", "order_total")
    presets = (
        Preset("all", "All"),
        Preset("authorize_net", "Authorize.Net — transactions", {
            "filters": [{"field": "method", "op": "eq", "value": "Authorize.Net"}, _SUCCESS], "sort": _NEWEST}),
        _by_settlement("authorize_net_settlements", "Authorize.Net"),
        _by_settlement("paypal_settlements", "PayPal"),
        Preset("paypal_weekly", "PayPal — weekly", {
            "filters": [{"field": "method", "op": "eq", "value": "PayPal"}, _SUCCESS],
            "group": [{"field": "processed_week", "desc": True}], "sort": _NEWEST}),
        Preset("alphapay_manual_monthly", "AlphaPay / Manual — monthly", {
            "filters": [{"field": "method", "op": "in", "value": ["AlphaPay", "Manual"]}, _SUCCESS],
            "group": [{"field": "processed_month", "desc": True}], "sort": _NEWEST}),
        Preset("exceptions", "Exceptions", {
            "filters": [{"field": "issue", "op": "not_null", "value": None},
                        {"field": "issue", "op": "not_in", "value": list(INFO_ISSUES)}],
            "group": [{"field": "issue", "desc": False}], "sort": _NEWEST}),
    )


class PaymentRowService(ReadOnlyService[PaymentRow, PaymentRowRead]):
    repo_cls = PaymentRowRepository
    read_schema = PaymentRowRead
    read_permission = READ
    resource = Resource(path="/payments", tags=("payments",))
