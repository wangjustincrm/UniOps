"""The Radiant inventory (design §15): the move ledger and its read models.

A move takes a quantity (EA) of an item — and, where the source names it, a lot and its expiry —
from one place to another. Places: PLANT (our plant), TO_RADIANT (a transfer on its way), RADIANT
(on hand at the 3PL), TO_CUSTOMER (shipped, delivery not final), CUSTOMER (delivered), RETURNING
(returned by the customer, not back at Radiant yet), ADJUSTED (a person's correction: written off
or found). Every figure is a sum of moves; a move is never changed."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Integer, String, Text, UniqueConstraint, Uuid, case, cast, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

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


def _net(place: str):
    """What a place holds: in minus out."""
    return func.coalesce(func.sum(case((StockMove.to_place == place, StockMove.qty_ea), else_=0)
                                  - case((StockMove.from_place == place, StockMove.qty_ea), else_=0)), 0)


_LOTS = (
    select(
        func.min(StockMove.id).label("id"),
        StockMove.item_code.label("item_code"),
        StockMove.lot.label("lot"),
        func.max(StockMove.expiry).label("expiry"),
        cast(func.max(StockMove.expiry) - func.current_date(), Integer).label("days_to_expiry"),
        _net(RADIANT).label("on_hand_ea"),
        _net(TO_CUSTOMER).label("to_customer_ea"),
        _net(RETURNING).label("returning_ea"),
        _net(TO_RADIANT).label("to_radiant_ea"),
        func.sum(case((StockMove.to_place == RADIANT, StockMove.qty_ea), else_=0)).label("received_ea"),
        func.sum(case((StockMove.from_place == RADIANT, StockMove.qty_ea), else_=0)).label("shipped_ea"),
        func.min(StockMove.at).label("first_move_at"),
        func.max(StockMove.at).label("last_move_at"),
    )
    .group_by(StockMove.item_code, StockMove.lot)
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

    moves = (select(StockMove.item_code.label("item_code"), _net(RADIANT).label("on_hand"), _net(TO_RADIANT).label("to_radiant"),
                    _net(TO_CUSTOMER).label("to_customer"), _net(RETURNING).label("returning"),
                    func.max(StockMove.at).label("last_move_at"))
             .group_by(StockMove.item_code).subquery("m"))
    held = (select(_LOTS.c.item_code.label("item_code"), func.count().label("lots"), func.min(_LOTS.c.expiry).label("first_expiry"))
            .where(_LOTS.c.on_hand_ea > 0).group_by(_LOTS.c.item_code).subquery("h"))
    # On a 940 that went out and no 945 has answered yet: promised to the order, still on the shelf.
    alloc = (select(OrderLine.item_code.label("item_code"), func.sum(OrderLine.qty_ea).label("allocated"))
             .join(Order, Order.id == OrderLine.order_id)
             .where(Order.fulfillment_status.in_([F.SENT_940.value, F.ACCEPTED_940.value, F.TIMEOUT_940.value]),
                    Order.historical.is_(False), Order.shadow.is_(False), OrderLine.removed.is_(False), OrderLine.item_code.is_not(None))
             .group_by(OrderLine.item_code).subquery("a"))
    on_hand = func.coalesce(moves.c.on_hand, 0)
    allocated = func.coalesce(alloc.c.allocated, 0)
    return (
        select(Item.id.label("id"), Item.item_code.label("item_code"), Item.description.label("description"),
               on_hand.label("on_hand_ea"), cast(allocated, Integer).label("allocated_ea"),
               cast(on_hand - allocated, Integer).label("available_ea"),
               func.coalesce(moves.c.to_radiant, 0).label("to_radiant_ea"), func.coalesce(moves.c.to_customer, 0).label("to_customer_ea"),
               func.coalesce(moves.c.returning, 0).label("returning_ea"), func.coalesce(held.c.lots, 0).label("lots"),
               held.c.first_expiry.label("first_expiry"),
               cast(held.c.first_expiry - func.current_date(), Integer).label("days_to_first_expiry"),
               moves.c.last_move_at.label("last_move_at"))
        .select_from(Item)
        .outerjoin(moves, moves.c.item_code == Item.item_code)
        .outerjoin(held, held.c.item_code == Item.item_code)
        .outerjoin(alloc, alloc.c.item_code == Item.item_code)
        .where((moves.c.item_code.is_not(None)) | (alloc.c.item_code.is_not(None)))
        .subquery("sales_stock_items")
    )


_ITEMS = _item_read_model()


class StockItem(Base):
    """One item: on hand at Radiant, allocated to 940s, available, and on its way (columns: _ITEMS's labels)."""
    __table__ = _ITEMS
    __mapper_args__ = {"primary_key": [_ITEMS.c.id]}
