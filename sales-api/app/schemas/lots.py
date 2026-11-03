"""Lots: a 945 row as a recall sees it (app/models/lots.py)."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class LotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lot: str | None
    item_code: str | None
    expiry: date | None
    days_to_expiry: int | None
    qty_shipped_ea: int
    sscc: str | None
    tracking: str | None
    shipment_id: int
    document_id: int
    ship_date: date | None
    bol: str | None
    our_reference: str | None
    applied: bool
    shadow: bool
    order_id: int | None
    order_ref: str | None
    partner_code: str | None
    partner_name: str | None
    ship_to_city: str | None
    ship_to_postal: str | None
