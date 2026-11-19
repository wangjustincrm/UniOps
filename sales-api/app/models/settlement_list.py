"""The Settlements list (Finance, user 2026-11-17): one row per deposit a gateway paid out — an
Authorize.Net settled batch — beside what Shopify says of the same money. A read model over
sales_gateway_settlements, sales_gateway_transactions and sales_order_payments; nothing stored.

- Sales / Refunds / Net: as the gateway counts them (its batch statistics).
- Matched net: the Shopify payments matched to the batch's settled transactions (sales − refunds).
- Difference: Net − Matched net. Zero when every settled transaction is a Shopify payment.
- Unmatched: settled transactions not matched to a Shopify payment once their details were read
  (Still matching: not read yet); Probable: matched by amount and time only.
- Chargebacks / returned items: from the batch statistics, not in Net; with Other (chargeback,
  returned-item … transactions) they explain a difference with the bank deposit.
- Issue: why Finance should look at the batch (its transactions not read yet, chargebacks or returns,
  unmatched, a difference, still matching), else empty.
"""
from __future__ import annotations

from sqlalchemy import Date, Integer, Numeric, case, cast, func, select

from app.db.base import Base
from app.domain.gateways import LABELS
from app.models.gateways import GatewaySettlement, GatewayTransaction, details_attempts
from app.models.payments import OrderPayment

ISSUES = ("Transactions not read yet", "Chargebacks or returns", "Unmatched transactions", "Differs from the transfer",
          "Differs from Shopify", "Still matching")
GATEWAY_NAMES = tuple(LABELS.values())

_TZ = "America/Toronto"
_S, _T, _P = GatewaySettlement, GatewayTransaction, OrderPayment

_per_batch = (
    select(
        _T.settlement_id.label("settlement_id"),
        func.count().filter(_T.settled.is_(True) & _T.payment_row_id.is_(None) & _T.kind.in_(("sale", "refund"))
                            & (_T.details_read_at.is_not(None) | (details_attempts(_T) >= 3))).label("unmatched_count"),
        func.count().filter(_T.settled.is_(True) & _T.payment_row_id.is_(None) & _T.kind.in_(("sale", "refund"))
                            & _T.details_read_at.is_(None) & (details_attempts(_T) < 3)).label("pending_count"),
        # Authorize.Net's other transactions are chargebacks / returned items; PayPal's other events (holds, deposits
        # from the bank, the transfer itself) are in the settlement's notes, its chargebacks in its own columns.
        func.count().filter((_T.kind == "other") & (_T.gateway == "AUTHORIZE_NET")).label("other_count"),
        func.count().filter(_T.match_method == "AMOUNT_TIME").label("probable_count"),
        func.count().filter(_T.settled.is_(True)).label("transaction_count"),
        func.coalesce(func.sum(case((_T.kind == "sale", _P.amount), (_T.kind == "refund", -_P.amount), else_=0))
                      .filter(_T.settled.is_(True)), 0).label("matched_net"),
    )
    .select_from(_T).outerjoin(_P, _P.id == _T.payment_row_id)
    .where(_T.settlement_id.is_not(None)).group_by(_T.settlement_id)
    .subquery("per_batch")
)

_B = _per_batch.c
_matched = func.coalesce(_B.matched_net, 0)
_unmatched = func.coalesce(_B.unmatched_count, 0)
_pending = func.coalesce(_B.pending_count, 0)
_other = func.coalesce(_B.other_count, 0)
_diff = _S.sales_amount - _S.refund_amount - _matched  # gateway sales − refunds against Shopify's (fees aside)
# PayPal (option A): what went to the bank, and that less the period's Net (a part of the balance, holds, a deposit …).
_transfer = cast(_S.raw["transfer_amount"].astext, Numeric(14, 2))
_carried = cast(_S.raw["carried_amount"].astext, Numeric(14, 2))

_ROWS = (
    select(
        _S.id.label("id"), _S.connection_id.label("connection_id"),
        case(*((_S.gateway == k, v) for k, v in LABELS.items()), else_=_S.gateway).label("gateway"),
        _S.external_id.label("batch_id"), _S.settled_at.label("settled_at"),
        cast(func.date_trunc("day", func.timezone(_TZ, _S.settled_at)), Date).label("settled_date"),
        cast(func.date_trunc("week", func.timezone(_TZ, _S.settled_at)), Date).label("settled_week"),
        cast(func.date_trunc("month", func.timezone(_TZ, _S.settled_at)), Date).label("settled_month"),
        _S.state.label("state"), _S.payment_method.label("payment_method"), _S.currency.label("currency"),
        _S.sales_amount.label("sales_amount"), _S.sales_count.label("sales_count"),
        _S.refund_amount.label("refund_amount"), _S.refund_count.label("refund_count"),
        _S.fee_amount.label("fee_amount"), _S.net_amount.label("net_amount"),
        _S.declined_count.label("declined_count"), _S.voided_count.label("voided_count"),
        _S.chargeback_amount.label("chargeback_amount"), _S.chargeback_count.label("chargeback_count"),
        _S.returned_amount.label("returned_amount"), _S.returned_count.label("returned_count"),
        cast(_other, Integer).label("other_count"), cast(_pending, Integer).label("pending_count"),
        cast(func.coalesce(_B.transaction_count, 0), Integer).label("transaction_count"),
        cast(_matched, Numeric(14, 2)).label("matched_net"), cast(_diff, Numeric(14, 2)).label("difference"),
        _transfer.label("transfer_amount"), _carried.label("carried_amount"), _S.raw["notes"].astext.label("notes"),
        cast(_unmatched, Integer).label("unmatched_count"),
        cast(func.coalesce(_B.probable_count, 0), Integer).label("probable_count"),
        _S.transactions_read_at.label("transactions_read_at"),
        case((_S.transactions_read_at.is_(None), "Transactions not read yet"),
             ((_S.chargeback_count + _S.returned_count + _other) > 0, "Chargebacks or returns"),
             (_unmatched > 0, "Unmatched transactions"), (_pending > 0, "Still matching"),
             (func.coalesce(_carried, 0) != 0, "Differs from the transfer"),
             (_diff != 0, "Differs from Shopify"), else_=None).label("issue"),
    )
    .select_from(_S).outerjoin(_per_batch, _B.settlement_id == _S.id)
    .subquery("sales_settlement_rows")
)


class SettlementRow(Base):
    """One deposit; columns: _ROWS's labels."""
    __table__ = _ROWS
    __mapper_args__ = {"primary_key": [_ROWS.c.id]}
