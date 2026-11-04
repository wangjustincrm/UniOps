"""The Order status rules (app/domain/order_status.py) as SQL: the same rules,
run over columns. What a list filters, sorts, groups and counts on."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sqlalchemy import and_, case, func, not_, or_

from app.domain.order_status import WAITING_TO_SEND, hold_rules, status_rules
from app.models import invoices, shipments  # noqa: F401 — map Order.has_945_sql and the other facts
from app.models.orders import Order


class Sql:
    """The algebra over column expressions."""
    @staticmethod
    def is_(value: Any, *values: Any):
        return value.in_(values)

    all_ = staticmethod(and_)
    any_ = staticmethod(or_)
    not_ = staticmethod(not_)


FACTS = SimpleNamespace(
    fulfillment=Order.fulfillment_status, invoice=Order.invoice_status, source_type=Order.source_type, shadow=Order.shadow,
    archived=Order.archived_in_shopify_at.is_not(None),
    fulfilled_in_shopify=func.coalesce(Order.shopify_fulfillment_status, "") == "fulfilled",
    cancelled_at_source=Order.source_cancelled_at.is_not(None),
    has_945=Order.has_945_sql, shopify_failed=Order.shopify_failed_sql, shopify_pending=Order.shopify_pending_sql,
    invoicing=func.coalesce(Order.partner_invoice_method_sql, "NONE") != "NONE",
)
_B = Sql()
ORDER_STATUS_SQL = case(*status_rules(FACTS, _B), else_=WAITING_TO_SEND)
ON_HOLD_REASON_SQL = case(*hold_rules(FACTS, _B), else_=None)
