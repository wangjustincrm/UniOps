"""Shopify finance (user 2026-11-03): an order's payments, refunds and its
financial summary, stored read-only from the store with every pass that sees
the order changed (a refund bumps `updated_at`), and filled for the orders
already here — a few per pass, oldest first — until none is missing.

Writes nothing to Shopify; reads `orders/{id}.json` and
`orders/{id}/transactions.json` (read_orders). Never touches the order's lines
or status (FR-OB-11b is the order sync's): only the finance columns, the
lines' tax and discount, and the payment / refund tables. Nothing is deleted.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select

from app.domain.shopify_finance import Finance, finance_of
from app.integrations.shopify import strip_card_data
from app.models.orders import Order, OrderLine
from app.models.payments import OrderPayment, OrderRefund, OrderRefundAdjustment, OrderRefundLine

REFRESH_PER_PASS = 50  # orders whose finance is missing or stale, filled per pass


async def store_finance(uow, order: Order, o: dict[str, Any], transactions: list[dict[str, Any]]) -> Finance:
    """Upsert the order's finance from its JSON and transactions (one unit of work)."""
    s = uow.session
    o, transactions = strip_card_data(o), strip_card_data(transactions)  # also when a caller forgot (never stored)
    f = finance_of(o, transactions)
    uow.set_reason("Shopify payments and refunds")
    order.source_name, order.tags, order.payment_gateways = f.source_name, f.tags, f.payment_gateways or None
    order.discount_codes, order.tax_lines = f.discount_codes or None, f.tax_lines or None
    order.original_total, order.total_refunded, order.net_amount = f.original_total, f.total_refunded, f.net_amount
    order.finance_updated_at = order.source_updated_at
    lines = {ln.line_no: ln for ln in (await s.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars()}
    for lid, lf in f.lines.items():
        ln = lines.get(lid)
        if ln is not None:
            ln.tax_amount, ln.discount_amount, ln.tax_lines = lf.tax_amount, lf.discount_amount, lf.tax_lines or None
    refund_ids: dict[int, int] = {}
    for r in f.refunds:
        row = (await s.execute(select(OrderRefund).where(OrderRefund.shopify_id == r.shopify_id))).scalar_one_or_none()
        if row is None:
            row = OrderRefund(order_id=order.id, shopify_id=r.shopify_id, raw=r.raw)
            s.add(row)
        row.refunded_at, row.processed_at, row.note, row.restock = r.refunded_at, r.processed_at, r.note, r.restock
        row.lines_amount, row.shipping_amount, row.adjustments_amount = r.lines_amount, r.shipping_amount, r.adjustments_amount
        row.amount, row.raw = r.amount, r.raw
        await s.flush()
        refund_ids[r.shopify_id] = row.id
        for rl in r.lines:
            line = (await s.execute(select(OrderRefundLine).where(OrderRefundLine.shopify_id == rl.shopify_id))).scalar_one_or_none()
            if line is None:
                line = OrderRefundLine(refund_id=row.id, shopify_id=rl.shopify_id)
                s.add(line)
            ol = lines.get(rl.line_item_id) if rl.line_item_id is not None else None
            line.line_item_id, line.order_line_id = rl.line_item_id, ol.id if ol else None
            line.sku, line.title, line.quantity = rl.sku, rl.title, rl.quantity
            line.subtotal, line.total_tax, line.restock_type = rl.subtotal, rl.total_tax, rl.restock_type
        for a in r.adjustments:
            adj = (await s.execute(select(OrderRefundAdjustment).where(OrderRefundAdjustment.shopify_id == a.shopify_id))).scalar_one_or_none()
            if adj is None:
                adj = OrderRefundAdjustment(refund_id=row.id, shopify_id=a.shopify_id)
                s.add(adj)
            adj.kind, adj.reason, adj.amount, adj.tax_amount = a.kind, a.reason, a.amount, a.tax_amount
    for p in f.payments:
        row = (await s.execute(select(OrderPayment).where(OrderPayment.shopify_id == p.shopify_id))).scalar_one_or_none()
        if row is None:
            row = OrderPayment(order_id=order.id, shopify_id=p.shopify_id, kind=p.kind, amount=p.amount, raw=p.raw)
            s.add(row)
        for k in ("parent_id", "kind", "gateway", "status", "amount", "currency", "processed_at", "authorization",
                  "payment_id", "error_code", "source_name", "test", "raw"):
            setattr(row, k, getattr(p, k))
        row.refund_id = refund_ids.get(p.refund_shopify_id) if p.refund_shopify_id else None
    await s.flush()
    return f


def stale_finance(conn_id: int):
    """The Shopify orders of a store whose finance is missing or older than the order."""
    return (select(Order.id, Order.source_order_id).where(
        Order.connection_id == conn_id, Order.source_type == "SHOPIFY",
        or_(Order.finance_updated_at.is_(None), Order.finance_updated_at != Order.source_updated_at))
        .order_by(Order.id))
