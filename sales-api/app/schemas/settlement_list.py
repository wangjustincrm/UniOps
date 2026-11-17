"""The Settlements list's row (app/models/settlement_list.py)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class SettlementRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    connection_id: int
    gateway: str
    batch_id: str
    settled_at: datetime
    settled_date: date
    settled_week: date
    settled_month: date
    state: str | None = None
    payment_method: str | None = None
    currency: str
    sales_amount: Decimal
    sales_count: int
    refund_amount: Decimal
    refund_count: int
    fee_amount: Decimal | None = None
    net_amount: Decimal
    declined_count: int
    voided_count: int
    chargeback_amount: Decimal
    chargeback_count: int
    returned_amount: Decimal
    returned_count: int
    other_count: int
    pending_count: int
    transaction_count: int
    matched_net: Decimal
    difference: Decimal
    unmatched_count: int
    probable_count: int
    transactions_read_at: datetime | None = None
    issue: str | None = None
