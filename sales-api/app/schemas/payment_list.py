"""The Payments list's row (app/models/payment_list.py)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PaymentRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    payment_row_id: int | None = None
    order_id: int
    order_ref: str | None = None
    customer: str | None = None
    processed_at: datetime | None = None
    processed_week: date | None = None
    processed_month: date | None = None
    method: str | None = None
    gateway: str | None = None
    kind: str
    status: str | None = None
    amount: Decimal
    currency: str
    sales_amount: Decimal
    refund_amount: Decimal
    net_amount: Decimal
    gateway_ref: str | None = None
    shopify_payment_id: str | None = None
    order_source: str
    financial_status: str | None = None
    order_cancelled: bool = False
    issue: str | None = None
    discount: Decimal | None = None
    shipping: Decimal | None = None
    order_total: Decimal | None = None
    test: bool = False
