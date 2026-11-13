"""The Radiant inventory (design §15): the move ledger and its read models.

A move takes a quantity (EA) of an item — and, where the source names it, a lot and its expiry —
from one place to another. Places: PLANT (our plant), TO_RADIANT (a transfer on its way), RADIANT
(on hand at the 3PL), TO_CUSTOMER (shipped, delivery not final), CUSTOMER (delivered), RETURNING
(returned by the customer, not back at Radiant yet), ADJUSTED (a person's correction: written off
or found). Every figure is a sum of moves; a move is never changed."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, case, cast, func, literal, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.platform.entities import Audited, Versioned

PLANT, TO_RADIANT, RADIANT, TO_CUSTOMER, CUSTOMER, RETURNING, ADJUSTED = (
    "PLANT", "TO_RADIANT", "RADIANT", "TO_CUSTOMER", "CUSTOMER", "RETURNING", "ADJUSTED")
PLACES = (PLANT, TO_RADIANT, RADIANT, TO_CUSTOMER, CUSTOMER, RETURNING, ADJUSTED)


class StockMove(Base):
    __tablename__ = "sales_stock_moves"
    __table_args__ = (UniqueConstraint("source_type", "source_key", name="uq_sales_stock_moves_source"),
                      Index("ix_sales_stock_moves_item_lot", "item_code", "lot"), Index("ix_sales_stock_moves_at", "at"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    item_code: Mapped[str] = mapped_column(String(50), nullable=False)
    lot: Mapped[str | None] = mapped_column(String(50), nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    qty_ea: Mapped[int] = mapped_column(Integer, nullable=False)
    from_place: Mapped[str] = mapped_column(String(20), nullable=False)
    to_place: Mapped[str] = mapped_column(String(20), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_key: Mapped[str] = mapped_column(String(60), nullable=False)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transfer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class StockCount(Audited, Versioned, Base):
    """Radiant's inventory report at a moment (sales30, user 2026-11-13): what the warehouse said it held,
    compared lot by lot with Sales' own figures. Applied, it becomes the starting point: at `counted_at`
    each lot is set to Radiant's figure by "count" moves, and every move after it adds up from there.
    The moves before stay as they are (the report shows the difference they make)."""
    __tablename__ = "sales_stock_counts"

    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="Radiant portal", server_default="Radiant portal")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("sales_documents.id"), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class StockCountLine(Base):
    """One item and lot of a count, as Radiant gave it (pallets of the same lot added up)."""
    __tablename__ = "sales_stock_count_lines"
    __table_args__ = (Index("ix_sales_stock_count_lines_count", "count_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    count_id: Mapped[int] = mapped_column(ForeignKey("sales_stock_counts.id", ondelete="CASCADE"), nullable=False)
    item_code: Mapped[str] = mapped_column(String(50), nullable=False)
    lot: Mapped[str | None] = mapped_column(String(50), nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    qty_ea: Mapped[int] = mapped_column(Integer, nullable=False)
    hold_ea: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # sales31: picked for an order and waiting to ship (the report's Allocation): {"<order number>": EA}
    picked: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))


def _net(place: str):
    """What a place holds: in minus out."""
    return func.coalesce(func.sum(case((StockMove.to_place == place, StockMove.qty_ea), else_=0)
                                  - case((StockMove.from_place == place, StockMove.qty_ea), else_=0)), 0)


RECEIVED_SOURCES = ("receipt_row",)                    # 944 rows of transfers
RETURNED_SOURCES = ("return_944",)                     # RA 944s: back from customers
SHIPPED_SOURCES = ("shipment_row", "order_line")       # 945 rows; orders shipped with no 945


# The start (user 2026-11-13): once a Radiant report is applied, a lot's figures start from it — its figure there
# counts as received, and only the moves after it add up — so received − shipped = on hand stays true. With no
# report applied, everything from the first data counts.
_LATEST_COUNT = (select(StockCount.id).where(StockCount.applied_at.is_not(None))
                 .order_by(StockCount.counted_at.desc(), StockCount.id.desc()).limit(1).scalar_subquery())
_START = func.coalesce(select(StockCount.counted_at).where(StockCount.id == _LATEST_COUNT).scalar_subquery(),
                       cast(literal("-infinity"), DateTime(timezone=True)))


def _in_from(sources: tuple[str, ...]):
    return func.coalesce(func.sum(case(((StockMove.to_place == RADIANT) & StockMove.source_type.in_(sources) & (StockMove.at > _START),
                                        StockMove.qty_ea), else_=0)), 0)


def _out_from(sources: tuple[str, ...]):
    return func.coalesce(func.sum(case(((StockMove.from_place == RADIANT) & StockMove.source_type.in_(sources) & (StockMove.at > _START),
                                        StockMove.qty_ea), else_=0)), 0)


_BY_LOT = (
    select(
        func.min(StockMove.id).label("id"),
        StockMove.item_code.label("item_code"),
        StockMove.lot.label("lot"),
        func.max(StockMove.expiry).label("expiry"),
        _net(RADIANT).label("on_hand_ea"),
        _net(TO_CUSTOMER).label("to_customer_ea"),
        _net(RETURNING).label("returning_ea"),
        _net(TO_RADIANT).label("to_radiant_ea"),
        _in_from(RECEIVED_SOURCES).label("received_ea"),
        _in_from(RETURNED_SOURCES).label("returned_ea"),
        # Shipped is net (user 2026-11-13): what a customer sent back (RA 944) comes off what was shipped.
        (_out_from(SHIPPED_SOURCES) - _in_from(RETURNED_SOURCES)).label("shipped_ea"),
        # The latest applied report's own moves (at its moment): Radiant − Sales there, per lot.
        func.coalesce(func.sum(case(((StockMove.source_type == "count") & (StockMove.at == _START),
                                     case((StockMove.to_place == RADIANT, StockMove.qty_ea), else_=-StockMove.qty_ea)), else_=0)), 0)
        .label("count_net"),
        func.min(StockMove.at).label("first_move_at"),
        func.max(StockMove.at).label("last_move_at"),
    )
    .group_by(StockMove.item_code, StockMove.lot)
    .subquery("l")
)
# Radiant's latest applied report, per lot: its figure (the start) and what it held on hold.
_REPORT = (select(StockCountLine.item_code.label("item_code"), StockCountLine.lot.label("lot"),
                  func.sum(StockCountLine.qty_ea).label("start"), func.sum(StockCountLine.hold_ea).label("hold"),
                  func.max(StockCountLine.expiry).label("expiry"))
           .where(StockCountLine.count_id == _LATEST_COUNT).group_by(StockCountLine.item_code, StockCountLine.lot).subquery("report"))
def _picked_open():
    """Per lot, what the latest applied report shows picked for orders that have not shipped yet (no 945): in process.
    Once the order's 945 is in, it is shipped and no longer counts here."""
    from app.domain.status import Fulfillment as F
    from app.models.orders import Order
    kv = func.jsonb_each_text(StockCountLine.picked).table_valued("key", "value", joins_implicitly=True).alias("kv")
    return (select(StockCountLine.item_code.label("item_code"), StockCountLine.lot.label("lot"),
                   func.sum(cast(kv.c.value, Integer)).label("picked"))
            .select_from(StockCountLine).join(kv, literal(True))
            .join(Order, cast(Order.order_number, String) == kv.c.key)
            .where(StockCountLine.count_id == _LATEST_COUNT,
                   Order.fulfillment_status.in_([F.SENT_940.value, F.ACCEPTED_940.value, F.TIMEOUT_940.value]))
            .group_by(StockCountLine.item_code, StockCountLine.lot).subquery("picked"))


_PICKED = _picked_open()
_in_process = func.least(func.coalesce(_PICKED.c.picked, 0), func.greatest(_BY_LOT.c.on_hand_ea, 0))
_start = func.coalesce(_REPORT.c.start, 0)
# A lot's expiry as its 945s gave it, else as Radiant's report gives it (a 944 carries none).
_expiry = func.coalesce(_BY_LOT.c.expiry, _REPORT.c.expiry)
_received = _start + _BY_LOT.c.received_ea
# On hold: never more than what is on hand now.
_hold = case((_BY_LOT.c.on_hand_ea > 0, func.least(func.coalesce(_REPORT.c.hold, 0), _BY_LOT.c.on_hand_ea)), else_=0)
_LOTS = (
    select(
        _BY_LOT.c.id, _BY_LOT.c.item_code, _BY_LOT.c.lot, _expiry.label("expiry"),
        cast(_expiry - func.current_date(), Integer).label("days_to_expiry"),
        cast(_received, Integer).label("received_ea"), _BY_LOT.c.returned_ea, _BY_LOT.c.shipped_ea,
        # people's adjustments after the start: what on hand holds beyond received − shipped
        cast(_BY_LOT.c.on_hand_ea - _received + _BY_LOT.c.shipped_ea, Integer).label("adjusted_ea"),
        _BY_LOT.c.on_hand_ea,
        # Sales − Radiant at the latest applied report (user 2026-11-13: kept in sight until every difference is settled)
        cast(-_BY_LOT.c.count_net, Integer).label("report_diff_ea"),
        cast(_hold, Integer).label("hold_ea"),
        cast(_in_process, Integer).label("in_process_ea"),
        cast(_BY_LOT.c.on_hand_ea - _hold - _in_process, Integer).label("available_ea"),
        _BY_LOT.c.to_customer_ea, _BY_LOT.c.returning_ea, _BY_LOT.c.to_radiant_ea,
        _BY_LOT.c.first_move_at, _BY_LOT.c.last_move_at,
    )
    .select_from(_BY_LOT)
    .outerjoin(_REPORT, (_REPORT.c.item_code == _BY_LOT.c.item_code)
               & (func.coalesce(_REPORT.c.lot, "") == func.coalesce(_BY_LOT.c.lot, "")))
    .outerjoin(_PICKED, (_PICKED.c.item_code == _BY_LOT.c.item_code)
               & (func.coalesce(_PICKED.c.lot, "") == func.coalesce(_BY_LOT.c.lot, "")))
    .subquery("sales_stock_lots")
)


class StockLot(Base):
    """One item and lot: what Radiant holds of it, and what is on its way (columns: _LOTS's labels)."""
    __table__ = _LOTS
    __mapper_args__ = {"primary_key": [_LOTS.c.id]}


def _item_read_model():
    from app.models.config import Item
    from app.models.orders import Order, OrderLine
    from app.domain.status import Fulfillment as F

    # An item adds up its open lots only (user 2026-11-13): a lot with nothing on hand is closed; what it once
    # received and shipped is in its own row, not in the item's.
    lots = (select(_LOTS.c.item_code.label("item_code"),
                   func.sum(case((_LOTS.c.on_hand_ea != 0, _LOTS.c.received_ea), else_=0)).label("received"),
                   func.sum(case((_LOTS.c.on_hand_ea != 0, _LOTS.c.returned_ea), else_=0)).label("returned"),
                   func.sum(case((_LOTS.c.on_hand_ea != 0, _LOTS.c.shipped_ea), else_=0)).label("shipped"),
                   func.sum(case((_LOTS.c.on_hand_ea != 0, _LOTS.c.adjusted_ea), else_=0)).label("adjusted"),
                   func.sum(_LOTS.c.on_hand_ea).label("on_hand"), func.sum(_LOTS.c.hold_ea).label("hold"),
                   func.sum(case((_LOTS.c.on_hand_ea != 0, _LOTS.c.report_diff_ea), else_=0)).label("report_diff"),
                   func.sum(_LOTS.c.to_radiant_ea).label("to_radiant"), func.sum(_LOTS.c.to_customer_ea).label("to_customer"),
                   func.sum(_LOTS.c.returning_ea).label("returning"), func.max(_LOTS.c.last_move_at).label("last_move_at"),
                   func.count().filter(_LOTS.c.on_hand_ea > 0).label("lots"),
                   func.min(_LOTS.c.expiry).filter(_LOTS.c.on_hand_ea > 0).label("first_expiry"))
            .group_by(_LOTS.c.item_code).subquery("m"))
    # On a 940 that went out and no 945 has answered yet: promised to the order, still on the shelf.
    alloc = (select(OrderLine.item_code.label("item_code"), func.sum(OrderLine.qty_ea).label("allocated"))
             .join(Order, Order.id == OrderLine.order_id)
             .where(Order.fulfillment_status.in_([F.SENT_940.value, F.ACCEPTED_940.value, F.TIMEOUT_940.value]),
                    Order.historical.is_(False), Order.shadow.is_(False), OrderLine.removed.is_(False), OrderLine.item_code.is_not(None))
             .group_by(OrderLine.item_code).subquery("a"))
    on_hand = func.coalesce(lots.c.on_hand, 0)
    hold = func.coalesce(lots.c.hold, 0)
    allocated = func.coalesce(alloc.c.allocated, 0)
    n = lambda c: cast(func.coalesce(c, 0), Integer)  # noqa: E731
    return (
        select(Item.id.label("id"), Item.item_code.label("item_code"), Item.description.label("description"),
               n(lots.c.received).label("received_ea"), n(lots.c.returned).label("returned_ea"), n(lots.c.shipped).label("shipped_ea"),
               n(lots.c.adjusted).label("adjusted_ea"), n(lots.c.report_diff).label("report_diff_ea"), cast(on_hand, Integer).label("on_hand_ea"), cast(hold, Integer).label("hold_ea"),
               cast(allocated, Integer).label("allocated_ea"), cast(on_hand - hold - allocated, Integer).label("available_ea"),
               n(lots.c.to_radiant).label("to_radiant_ea"), n(lots.c.to_customer).label("to_customer_ea"),
               n(lots.c.returning).label("returning_ea"), n(lots.c.lots).label("lots"),
               lots.c.first_expiry.label("first_expiry"),
               cast(lots.c.first_expiry - func.current_date(), Integer).label("days_to_first_expiry"),
               lots.c.last_move_at.label("last_move_at"))
        .select_from(Item)
        .outerjoin(lots, lots.c.item_code == Item.item_code)
        .outerjoin(alloc, alloc.c.item_code == Item.item_code)
        .where((lots.c.item_code.is_not(None)) | (alloc.c.item_code.is_not(None)))
        .subquery("sales_stock_items")
    )


_ITEMS = _item_read_model()


class StockItem(Base):
    """One item: on hand at Radiant, allocated to 940s, available, and on its way (columns: _ITEMS's labels)."""
    __table__ = _ITEMS
    __mapper_args__ = {"primary_key": [_ITEMS.c.id]}

