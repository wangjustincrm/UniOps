"""The Payments list (Finance, user 2026-11-16): one row per Shopify payment or refund
transaction, plus one row for each Shopify order that should have been paid and has no
transaction at all (so the list's Exceptions can show it). A read model over
sales_order_payments and sales_orders, nothing stored.

- Method: Authorize.Net (Shopify's old "authorize_net" gateway and its new "authorize.net"
  app are one), PayPal, AlphaPay, Manual; anything else as Shopify names it.
- Gateway ID: the processor's own transaction id where Shopify gives it — the old
  Authorize.Net gateway's `authorization` ("121004214042#2281#purchase" → 121004214042) and
  PayPal's (its capture / refund id). Shopify's Authorize.Net app (from 2025-09-18) and
  AlphaPay give none, in REST or GraphQL; the Shopify payment id is their reference.
- Sales / Refunds / Net: successful sales and captures, successful refunds; per row, so the
  list's totals and group subtotals add them up.
- Order source: Subscription (Seal: a subscription_contract source or a seal_ tag) or One-off.
- Issue: why Finance should look at the row (the Exceptions view), else empty.
"""
from __future__ import annotations

from sqlalchemy import Date, Integer, Numeric, String, case, cast, func, literal, null, or_, select, union_all

from app.db.base import Base
from app.models.orders import Order
from app.models.payments import OrderPayment

ISSUES = ("Pending over 7 days", "Cancelled, not fully refunded", "Partially paid", "Failed on an open order",
          "Paid, no payment")
METHODS = ("Authorize.Net", "PayPal", "AlphaPay", "Manual")
KINDS = ("sale", "capture", "authorization", "refund", "void", "none")
STATUSES = ("success", "pending", "failure", "error")
SOURCES = ("Subscription", "One-off")

_TZ = "America/Toronto"
_SUCCESS_IN = ("sale", "capture")
# An order still owed money (a failed payment matters there): not cancelled and not paid yet.
_OPEN = ("pending", "partially_paid", "authorized")


def _method(gateway):
    g = func.lower(func.coalesce(gateway, ""))
    return case((g.in_(("authorize_net", "authorize.net")), "Authorize.Net"), (g == "paypal", "PayPal"),
                (g == "alphapay", "AlphaPay"), (g == "manual", "Manual"), else_=gateway)


def _local(ts, unit: str):
    return cast(func.date_trunc(unit, func.timezone(_TZ, ts)), Date)


def _source(o):
    return case((or_(o.source_name.like("subscription_contract%"), func.coalesce(o.tags, "").op("~")("(^|,\\s*)seal_")),
                 "Subscription"), else_="One-off")


def _customer(o):
    return func.coalesce(func.nullif(o.bill_to["name"].astext, ""), func.nullif(o.ship_to["name"].astext, ""))


_P, _O = OrderPayment, Order
_cancelled = _O.source_cancelled_at.is_not(None)
_at = func.coalesce(_P.processed_at, _P.created_at)
_ok_in = _P.kind.in_(_SUCCESS_IN) & (_P.status == "success")
_ok_refund = (_P.kind == "refund") & (_P.status == "success")

_PAYMENTS = (
    select(
        _P.id.label("id"), _P.id.label("payment_row_id"), _O.id.label("order_id"), _O.source_reference.label("order_ref"),
        _customer(_O).label("customer"), _method(_P.gateway).label("method"), _P.gateway.label("gateway"),
        _P.kind.label("kind"), _P.status.label("status"), _P.amount.label("amount"), func.coalesce(_P.currency, _O.currency).label("currency"),
        case((_ok_in, _P.amount), else_=0).label("sales_amount"),
        case((_ok_refund, _P.amount), else_=0).label("refund_amount"),
        case((_ok_in, _P.amount), (_ok_refund, -_P.amount), else_=0).label("net_amount"),
        _at.label("processed_at"), _local(_at, "week").label("processed_week"), _local(_at, "month").label("processed_month"),
        case((func.lower(_P.gateway) == "authorize_net", func.nullif(func.split_part(_P.authorization, "#", 1), "")),
             (func.lower(_P.gateway) == "paypal", func.nullif(_P.authorization, "")), else_=null()).label("gateway_ref"),
        _P.payment_id.label("shopify_payment_id"), _source(_O).label("order_source"),
        _O.shopify_financial_status.label("financial_status"), _cancelled.label("order_cancelled"),
        _O.discount.label("discount"), _O.shipping.label("shipping"), _O.original_total.label("order_total"), _P.test.label("test"),
        case(((_P.status == "pending") & (_at < func.now() - func.make_interval(0, 0, 0, 7)), "Pending over 7 days"),
             (_cancelled & _ok_in & func.coalesce(_O.shopify_financial_status, "").not_in(("refunded", "voided")),
              "Cancelled, not fully refunded"),
             ((_O.shopify_financial_status == "partially_paid") & _P.kind.in_(_SUCCESS_IN), "Partially paid"),
             (_P.status.in_(("failure", "error")) & ~_cancelled & _O.shopify_financial_status.in_(_OPEN), "Failed on an open order"),
             else_=null()).label("issue"),
    )
    .join(_O, _O.id == _P.order_id)
)

# An order Shopify calls paid (or partly) for more than nothing, with no transaction at all.
_NO_PAYMENT = (
    select(
        (-_O.id).label("id"), cast(null(), Integer).label("payment_row_id"), _O.id.label("order_id"), _O.source_reference.label("order_ref"),
        _customer(_O).label("customer"), cast(null(), String).label("method"), cast(null(), String).label("gateway"),
        literal("none").label("kind"), cast(null(), String).label("status"), cast(literal(0), Numeric(14, 2)).label("amount"),
        _O.currency.label("currency"), cast(literal(0), Numeric(14, 2)).label("sales_amount"),
        cast(literal(0), Numeric(14, 2)).label("refund_amount"), cast(literal(0), Numeric(14, 2)).label("net_amount"),
        _O.first_received_at.label("processed_at"), _local(_O.first_received_at, "week").label("processed_week"),
        _local(_O.first_received_at, "month").label("processed_month"),
        cast(null(), String).label("gateway_ref"), cast(null(), String).label("shopify_payment_id"), _source(_O).label("order_source"),
        _O.shopify_financial_status.label("financial_status"), _cancelled.label("order_cancelled"),
        _O.discount.label("discount"), _O.shipping.label("shipping"), _O.original_total.label("order_total"), literal(False).label("test"),
        case((_O.shopify_financial_status == "partially_paid", "Partially paid"), else_="Paid, no payment").label("issue"),
    )
    .where(_O.source_type == "SHOPIFY", _O.shopify_financial_status.in_(("paid", "partially_paid")),
           func.coalesce(_O.original_total, 0) > 0, ~select(_P.id).where(_P.order_id == _O.id).exists())
)

_ROWS = union_all(_PAYMENTS, _NO_PAYMENT).subquery("sales_payment_rows")


class PaymentRow(Base):
    """One payment or refund transaction (or an order with none); columns: _ROWS's labels."""
    __table__ = _ROWS
    __mapper_args__ = {"primary_key": [_ROWS.c.id]}
