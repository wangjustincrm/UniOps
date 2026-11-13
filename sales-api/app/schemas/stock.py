"""The Radiant inventory as read (design §15)."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class StockItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item_code: str
    description: str | None = None
    received_ea: int = 0
    returned_ea: int = 0
    shipped_ea: int = 0
    adjusted_ea: int = 0
    report_diff_ea: int = 0   # Sales − Radiant at the latest applied report (its open lots)
    on_hand_ea: int
    hold_ea: int = 0
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
    received_ea: int
    returned_ea: int = 0
    shipped_ea: int
    adjusted_ea: int = 0
    report_diff_ea: int = 0   # Sales − Radiant at the latest applied report
    on_hand_ea: int
    hold_ea: int = 0
    available_ea: int = 0
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


class StockCountRead(BaseModel):
    """Radiant's inventory report at a moment (sales30)."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    counted_at: datetime
    source: str
    note: str | None = None
    file_name: str | None = None
    document_id: int | None = None
    applied_at: datetime | None = None
    applied_by_name: str | None = None
    lines: int = 0
    total_ea: int = 0
    hold_ea: int = 0
    version: int
    created_at: datetime
    created_by_name: str
    updated_at: datetime | None = None
    updated_by_name: str | None = None


class StockCountLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item_code: str
    lot: str | None = None
    expiry: date | None = None
    qty_ea: int
    hold_ea: int
    description: str | None = None


class CountCompareRow(BaseModel):
    """One item and lot: Sales' moves up to the count, Radiant's figure, the difference, and after it."""
    item_code: str
    lot: str | None = None
    description: str | None = None
    known_item: bool = True
    expiry: date | None = None
    days_to_expiry: int | None = None
    in_ea: int = 0            # received + returned, up to the count
    received_ea: int = 0      # 944 rows (transfers)
    returned_ea: int = 0      # RA 944s (customer returns), already taken off shipped
    shipped_ea: int = 0       # 945 rows (packing lists) and orders shipped with no 945, less returns
    adjusted_ea: int = 0      # people's adjustments, earlier counts
    sales_ea: int = 0         # remaining by Sales at the count: in − out ± adjusted
    radiant_ea: int = 0
    hold_ea: int = 0
    in_report: bool = False
    difference_ea: int = 0    # Sales − Radiant
    in_after_ea: int = 0      # came in after the count (944, RA)
    out_after_ea: int = 0     # went out after the count (945, adjustments)
    since_ea: int = 0         # in after − out after
    now_ea: int = 0           # total now: Radiant + in after − out after (the figure once the count is the start)
    available_ea: int = 0     # at the report: Radiant − on hold
    open: bool = False        # in Radiant's report, holding stock, or moved after it; a closed lot counts in no total
    kind: str = "match"       # match | differs | sales_only | radiant_only | no_lot


class CountUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    counted_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


class CountVersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int


class CountUndoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    reason: str = Field(min_length=3, max_length=500)
