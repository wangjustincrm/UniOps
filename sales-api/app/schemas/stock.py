"""The Radiant inventory as read (design §15)."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class StockItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item_code: str
    description: str | None = None
    on_hand_ea: int
    allocated_ea: int
    available_ea: int
    to_radiant_ea: int
    to_customer_ea: int
    returning_ea: int
    lots: int
    first_expiry: date | None = None
    days_to_first_expiry: int | None = None
    last_move_at: datetime | None = None


class StockLotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item_code: str
    lot: str | None = None
    expiry: date | None = None
    days_to_expiry: int | None = None
    on_hand_ea: int
    received_ea: int
    shipped_ea: int
    to_customer_ea: int
    returning_ea: int
    to_radiant_ea: int
    first_move_at: datetime | None = None
    last_move_at: datetime | None = None


class StockMoveRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    at: datetime
    item_code: str
    lot: str | None = None
    expiry: date | None = None
    qty_ea: int
    from_place: str
    to_place: str
    source_type: str
    order_id: int | None = None
    transfer_id: int | None = None
    document_id: int | None = None
    reason: str | None = None
    created_by_name: str | None = None


class AdjustIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_code: str = Field(min_length=1, max_length=50)
    lot: str | None = Field(default=None, max_length=50)
    qty_ea: int = Field(ge=-10_000_000, le=10_000_000)
    reason: str = Field(min_length=3, max_length=500)
