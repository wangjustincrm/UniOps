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
- From the gateway (Finance, user 2026-11-17; sales32): the gateway transaction matched to the
  payment — its id (now also for Shopify's Authorize.Net app), status, card type, how it was matched
  (AMOUNT_TIME is probable only) and the settlement it was paid out in (batch id and date). A
  settled gateway transaction that is no Shopify payment is a row of its own (no order), so the
  Exceptions view shows it; a Shopify Authorize.Net payment the gateway never settled, once the
  gateway's data covers its date, is an issue too.
- Voided (review 2026-11-17): Shopify records a void as a sale plus a refund. The old Authorize.Net
  gateway marks the refund's authorization "…#void" (same id as the sale); with the new app the sale
  is matched to a voided gateway transaction, and a refund of the same amount on the order is its
  pair. Both rows are "Voided": no money (Sales / Refunds / Net 0) and never "Not settled".
"""
from __future__ import annotations

from sqlalchemy import Date, DateTime, Integer, Numeric, String, case, cast, func, literal, null, or_, select, union_all
from sqlalchemy.orm import aliased

from app.db.base import Base
from app.domain.gateways import LABELS
from app.models.gateways import GatewaySettlement, GatewayTransaction, details_attempts
from app.models.orders import Order
from app.models.payments import OrderPayment

ISSUES = ("Pending over 7 days", "Cancelled, not fully refunded", "Partially paid", "Failed on an open order",
          "Paid, no payment", "Not settled in the gateway", "In the gateway, not in Shopify",
          "Details not available from the gateway")
DETAILS_ATTEMPTS = 3  # services/gateway_intake.py: then its details are given up on
# How the gateway transaction was matched, in words (domain/gateway_match.py's methods).
MATCH_WORDS = {"TRANSACTION_ID": "Transaction ID", "SHOPIFY_PAYMENT_ID": "Shopify payment ID", "ORDER_NUMBER": "Order number",
               "AMOUNT_TIME": "Amount and time (probable)"}
MATCHES = tuple(MATCH_WORDS.values())
# A payment this much older than the gateway's last settlement should have been settled by it.
SETTLE_GRACE_DAYS = 3
# The id of a gateway-only row: below every order's negative id.
GATEWAY_ROW_OFFSET = 1_000_000_000
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


def _gateway_columns(t, s):
    words = case(*((t.match_method == k, v) for k, v in MATCH_WORDS.items()), else_=t.match_method)
    return (t.status.label("gateway_status"), t.card_type.label("card_type"), words.label("match_method"),
            t.fee_amount.label("gateway_fee"),
            s.id.label("settlement_id"), s.external_id.label("settlement_ref"), s.settled_at.label("settled_at"),
            _local(s.settled_at, "day").label("settled_date"))


def _no_gateway_columns():
    return (cast(null(), String).label("gateway_status"), cast(null(), String).label("card_type"),
            cast(null(), String).label("match_method"), cast(null(), Numeric(14, 2)).label("gateway_fee"), cast(null(), Integer).label("settlement_id"),
            cast(null(), String).label("settlement_ref"), cast(null(), DateTime(timezone=True)).label("settled_at"),
            cast(null(), Date).label("settled_date"))


def _customer(o):
    return func.coalesce(func.nullif(o.bill_to["name"].astext, ""), func.nullif(o.ship_to["name"].astext, ""))


_P, _O, _T, _S = OrderPayment, Order, GatewayTransaction, GatewaySettlement
_cancelled = _O.source_cancelled_at.is_not(None)
_at = func.coalesce(_P.processed_at, _P.created_at)
_ok_in = _P.kind.in_(_SUCCESS_IN) & (_P.status == "success")
_ok_refund = (_P.kind == "refund") & (_P.status == "success")
_is_anet = func.lower(func.coalesce(_P.gateway, "")).in_(("authorize_net", "authorize.net"))
_is_paypal = func.lower(func.coalesce(_P.gateway, "")) == "paypal"
# The stretch the gateway's data covers (none read: no issue is raised): Authorize.Net's settlements; PayPal's
# transactions (it reports each one, settled into the balance at once).
_covered_from = select(func.min(_S.settled_at)).where(_S.gateway == "AUTHORIZE_NET").scalar_subquery()
_covered_to = select(func.max(_S.settled_at)).where(_S.gateway == "AUTHORIZE_NET").scalar_subquery()
_T3 = aliased(GatewayTransaction)
_paypal_from = select(func.min(_T3.submitted_at)).where(_T3.gateway == "PAYPAL").scalar_subquery()
_paypal_to = select(func.max(_T3.submitted_at)).where(_T3.gateway == "PAYPAL").scalar_subquery()
# Voided in the gateway (see the module text): the old gateway's "#void" pair, or the new app's sale
# matched to a voided transaction and the refund of the same amount on its order.
_P2, _T2 = aliased(OrderPayment), aliased(GatewayTransaction)
_old_void = (func.lower(func.coalesce(_P.gateway, "")) == "authorize_net") & select(_P2.id).where(
    _P2.order_id == _P.order_id, _P2.status == "success", _P2.authorization.like("%#void"),
    func.split_part(_P2.authorization, "#", 1) == func.split_part(_P.authorization, "#", 1)).exists()
_new_void_refund = (_P.kind == "refund") & select(_P2.id).join(_T2, _T2.payment_row_id == _P2.id).where(
    _P2.order_id == _P.order_id, _P2.kind.in_(_SUCCESS_IN), _T2.kind == "void", _P2.amount == _P.amount).exists()
_voided = func.coalesce((_P.status == "success") & or_(_old_void, _T.kind == "void", _new_void_refund), False)
_not_settled = ((_ok_in | _ok_refund) & _T.id.is_(None) & ~_P.test & ~_voided & or_(
    _is_anet & (_at >= _covered_from) & (_at < _covered_to - func.make_interval(0, 0, 0, SETTLE_GRACE_DAYS)),
    _is_paypal & (_at >= _paypal_from) & (_at < _paypal_to - func.make_interval(0, 0, 0, SETTLE_GRACE_DAYS))))
# A pending payment that went through later (user 2026-11-19: SHP-1004, SHP-1013): the same order has a later
# successful payment of the same kind, amount and gateway id — no customer is waiting, so it is no issue.
_P4 = aliased(OrderPayment)
_superseded = select(_P4.id).where(
    _P4.order_id == _P.order_id, _P4.id != _P.id, _P4.status == "success", _P4.kind == _P.kind, _P4.amount == _P.amount,
    func.coalesce(_P4.authorization, "") == func.coalesce(_P.authorization, ""),
    func.coalesce(_P4.processed_at, _P4.created_at) >= _at).exists()
# A charge that never happened (user 2026-11-19: 21 Authorize.Net sales of Nov 2025): pending, on an order cancelled at
# source, and no gateway transaction for it — no money moved and none will. A pending refund stays an issue.
_never_captured = _P.kind.in_(("sale", "authorization")) & _cancelled & _T.id.is_(None)
_money_in = _ok_in & ~_voided
_money_back = _ok_refund & ~_voided

_PAYMENTS = (
    select(
        _P.id.label("id"), _P.id.label("payment_row_id"), _O.id.label("order_id"), _O.source_reference.label("order_ref"),
        _customer(_O).label("customer"), _method(_P.gateway).label("method"), _P.gateway.label("gateway"),
        _P.kind.label("kind"), _P.status.label("status"), _P.amount.label("amount"), func.coalesce(_P.currency, _O.currency).label("currency"),
        case((_money_in, _P.amount), else_=0).label("sales_amount"),
        case((_money_back, _P.amount), else_=0).label("refund_amount"),
        case((_money_in, _P.amount), (_money_back, -_P.amount), else_=0).label("net_amount"),
        (case((_money_in, _P.amount), (_money_back, -_P.amount), else_=0) - func.coalesce(_T.fee_amount, 0)).label("net_after_fee"),
        _at.label("processed_at"), _local(_at, "week").label("processed_week"), _local(_at, "month").label("processed_month"),
        func.coalesce(
            case((func.lower(_P.gateway) == "authorize_net", func.nullif(func.split_part(_P.authorization, "#", 1), "")),
                 (func.lower(_P.gateway) == "paypal", func.nullif(_P.authorization, "")), else_=null()),
            _T.external_id).label("gateway_ref"),
        _P.payment_id.label("shopify_payment_id"), _source(_O).label("order_source"),
        _O.shopify_financial_status.label("financial_status"), _cancelled.label("order_cancelled"),
        _O.discount.label("discount"), _O.shipping.label("shipping"), _O.original_total.label("order_total"), _P.test.label("test"),
        case(((_P.status == "pending") & (_at < func.now() - func.make_interval(0, 0, 0, 7)) & ~_superseded & ~_never_captured,
              "Pending over 7 days"),
             (_cancelled & _ok_in & func.coalesce(_O.shopify_financial_status, "").not_in(("refunded", "voided")),
              "Cancelled, not fully refunded"),
             ((_O.shopify_financial_status == "partially_paid") & _P.kind.in_(_SUCCESS_IN), "Partially paid"),
             (_P.status.in_(("failure", "error")) & ~_cancelled & _O.shopify_financial_status.in_(_OPEN), "Failed on an open order"),
             (_not_settled, "Not settled in the gateway"),
             else_=null()).label("issue"),
        *_gateway_columns(_T, _S), case((_voided, True), else_=False).label("voided"),
    )
    .join(_O, _O.id == _P.order_id)
    .outerjoin(_T, _T.payment_row_id == _P.id)
    .outerjoin(_S, _S.id == _T.settlement_id)
)

# An order Shopify calls paid (or partly) for more than nothing, with no transaction at all.
_NO_PAYMENT = (
    select(
        (-_O.id).label("id"), cast(null(), Integer).label("payment_row_id"), _O.id.label("order_id"), _O.source_reference.label("order_ref"),
        _customer(_O).label("customer"), cast(null(), String).label("method"), cast(null(), String).label("gateway"),
        literal("none").label("kind"), cast(null(), String).label("status"), cast(literal(0), Numeric(14, 2)).label("amount"),
        _O.currency.label("currency"), cast(literal(0), Numeric(14, 2)).label("sales_amount"),
        cast(literal(0), Numeric(14, 2)).label("refund_amount"), cast(literal(0), Numeric(14, 2)).label("net_amount"),
        cast(literal(0), Numeric(14, 2)).label("net_after_fee"),
        _O.first_received_at.label("processed_at"), _local(_O.first_received_at, "week").label("processed_week"),
        _local(_O.first_received_at, "month").label("processed_month"),
        cast(null(), String).label("gateway_ref"), cast(null(), String).label("shopify_payment_id"), _source(_O).label("order_source"),
        _O.shopify_financial_status.label("financial_status"), _cancelled.label("order_cancelled"),
        _O.discount.label("discount"), _O.shipping.label("shipping"), _O.original_total.label("order_total"), literal(False).label("test"),
        case((_O.shopify_financial_status == "partially_paid", "Partially paid"), else_="Paid, no payment").label("issue"),
        *_no_gateway_columns(), literal(False).label("voided"),
    )
    .where(_O.source_type == "SHOPIFY", _O.shopify_financial_status.in_(("paid", "partially_paid")),
           func.coalesce(_O.original_total, 0) > 0, ~select(_P.id).where(_P.order_id == _O.id).exists())
)

# A settled gateway transaction that is no Shopify payment (a sale typed in the gateway, a payment
# Shopify does not show): a row of its own, so Finance sees all the money that was paid out.
_GATEWAY_ONLY = (
    select(
        (-(_T.id + GATEWAY_ROW_OFFSET)).label("id"), cast(null(), Integer).label("payment_row_id"),
        cast(null(), Integer).label("order_id"), cast(null(), String).label("order_ref"), cast(null(), String).label("customer"),
        case(*((_T.gateway == k, v) for k, v in LABELS.items()), else_=_T.gateway).label("method"),
        cast(null(), String).label("gateway"), _T.kind.label("kind"),
        literal("success").label("status"), _T.amount.label("amount"), _T.currency.label("currency"),
        case((_T.kind == "sale", _T.amount), else_=0).label("sales_amount"),
        case((_T.kind == "refund", _T.amount), else_=0).label("refund_amount"),
        case((_T.kind == "sale", _T.amount), (_T.kind == "refund", -_T.amount), else_=0).label("net_amount"),
        (case((_T.kind == "sale", _T.amount), (_T.kind == "refund", -_T.amount), else_=0)
         - func.coalesce(_T.fee_amount, 0)).label("net_after_fee"),
        func.coalesce(_T.submitted_at, _S.settled_at).label("processed_at"),
        _local(func.coalesce(_T.submitted_at, _S.settled_at), "week").label("processed_week"),
        _local(func.coalesce(_T.submitted_at, _S.settled_at), "month").label("processed_month"),
        _T.external_id.label("gateway_ref"), cast(null(), String).label("shopify_payment_id"),
        cast(null(), String).label("order_source"), cast(null(), String).label("financial_status"),
        literal(False).label("order_cancelled"), cast(null(), Numeric(14, 2)).label("discount"),
        cast(null(), Numeric(14, 2)).label("shipping"), cast(null(), Numeric(14, 2)).label("order_total"),
        literal(False).label("test"),
        case((_T.details_read_at.is_(None), "Details not available from the gateway"),
             else_="In the gateway, not in Shopify").label("issue"),
        *_gateway_columns(_T, _S), literal(False).label("voided"),
    )
    .select_from(_T).outerjoin(_S, _S.id == _T.settlement_id)
    # Only once nothing better can come (its details were read and matching ran again): a first
    # history read shows no transaction here that a later pass will match.
    .where(_T.payment_row_id.is_(None), _T.settled.is_(True), _T.kind.in_(("sale", "refund")),
           or_(_T.details_read_at.is_not(None), details_attempts(_T) >= DETAILS_ATTEMPTS))
)

_ROWS = union_all(_PAYMENTS, _NO_PAYMENT, _GATEWAY_ONLY).subquery("sales_payment_rows")


class PaymentRow(Base):
    """One payment or refund transaction (or an order with none); columns: _ROWS's labels."""
    __table__ = _ROWS
    __mapper_args__ = {"primary_key": [_ROWS.c.id]}
