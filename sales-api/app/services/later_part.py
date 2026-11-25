"""A later 945 of an order that shipped (user 2026-11-25): its facts for the rule (domain/later_part.py), and booking it.

The 945 pass adds the part the first 945 left; a person adds that part, or one that is more than the 940 carried, in Compare the
945s. Both book it here: the quantities add up on the lines, its rows join their lines, the shipment is applied. Shopify is told
nothing and nobody is emailed (the order was fulfilled there in full at its 997, or by its first 945); Sales shows both tracking
numbers. The caller holds the order's lock and takes lines → shipment → rows after it, here.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import time

from sqlalchemy import func, select

from app.domain.later_part import PRESS, Answer, Facts, ItemFigures, above_words, answer
from app.domain.row_matching import Group, Row, line_identifiers, match_rows
from app.domain.status import FULFILLMENT
from app.domain.status import Fulfillment as F
from app.models.config import Item
from app.models.documents import Document
from app.models.invoices import DeliveryConfirmation, Invoice, OrderReturn
from app.models.orders import Order, OrderLine
from app.models.payments import OrderRefund, OrderRefundLine
from app.models.shipments import Shipment, ShipmentRow
from app.models.stock import RADIANT, StockMove
from app.platform.clock import clock
from app.platform.errors import BusinessError
from app.services import oplog
from app.services.outbound import latest_940
from app.services.problems import EDI_945_VALIDATION, SECOND_945, SHORT_SHIPMENT, problem

STEP = "945"


def shipment_content(ship_date, rows) -> tuple:
    """What a 945 says shipped, to tell a resend from a second shipment: the date and every row's
    item, EA, lot, expiry and parcel, in any order."""
    return ship_date, sorted((str(i or ""), int(q or 0), str(lot or ""), str(exp or ""), str(t or "")) for i, q, lot, exp, t in rows)


async def stored_content(s, shipment_id: int, ship_date) -> tuple:
    rows = (await s.execute(select(ShipmentRow.item_code, ShipmentRow.qty_shipped_ea, ShipmentRow.lot, ShipmentRow.expiry,
                                   ShipmentRow.tracking).where(ShipmentRow.shipment_id == shipment_id))).all()
    return shipment_content(ship_date, [tuple(r) for r in rows])


def later_ones(applied: Sequence[Shipment]) -> list[Shipment]:
    """Of an order's applied 945s, oldest first, those that came after the first one's file: nothing goes to Shopify for them."""
    return [x for x in applied if x.document_id != applied[0].document_id] if applied else []


async def applied_945s(s, order_id: int) -> list[Shipment]:
    return list((await s.execute(select(Shipment).where(Shipment.order_id == order_id, Shipment.applied.is_(True))
                                 .order_by(Shipment.id))).scalars())


async def waiting_945s(s, order_id: int) -> list[tuple[Shipment, str]]:
    """The order's 945s that are not applied and differ from every applied one (and from one another), oldest first, with their
    file's name. A resend is not one of them."""
    rows = (await s.execute(select(Shipment, Document.remote_name).join(Document, Document.id == Shipment.document_id)
                            .where(Shipment.order_id == order_id, Shipment.shadow.is_(False)).order_by(Shipment.id))).all()
    seen = [await stored_content(s, x.id, x.ship_date) for x, _ in rows if x.applied]
    out = []
    for x, name in rows:
        if x.applied:
            continue
        mine = await stored_content(s, x.id, x.ship_date)
        if mine not in seen:
            seen.append(mine)
            out.append((x, name or "?"))
    return out


async def stored_rows(s, shipment_id: int, *, for_update: bool = False) -> list[ShipmentRow]:
    q = select(ShipmentRow).where(ShipmentRow.shipment_id == shipment_id).order_by(ShipmentRow.lx_index, ShipmentRow.w12_index)
    if for_update:
        q = q.with_for_update().execution_options(populate_existing=True)
    return list((await s.execute(q)).scalars())


def as_rows(rows: Sequence[ShipmentRow]) -> list[Row]:
    return [Row((r.lx_index, r.w12_index), r.item_code, r.upc, r.qty_shipped_ea) for r in rows]


@dataclass
class Judged:
    """The rule's answer for one 945, and what booking it needs."""
    answer: Answer
    lines: list[OrderLine] = field(default_factory=list)          # the order's warehouse lines, in the 940's order
    carried: dict[int, int] = field(default_factory=dict)         # line id → EA on the 940
    groups: list[Group] = field(default_factory=list)

    @property
    def links(self) -> dict[tuple[int, int], int]:
        return {at: lid for g in self.groups for at, lid in g.links.items()}

    def now(self, ln: OrderLine) -> int:
        """EA the line has shipped once this 945 counts."""
        return (ln.shipped_ea or 0) + sum(g.shipped.get(ln.id, 0) for g in self.groups)

    @property
    def short(self) -> list[str]:
        return [f"{ln.item_code}: {self.carried[ln.id] - self.now(ln)} EA not shipped ({self.now(ln)} of {self.carried[ln.id]})"
                for ln in self.lines if self.carried[ln.id] > self.now(ln)]


async def judge(s, order: Order, rows: Sequence[Row], *, by: str, shipment_id: int | None = None, for_update: bool = False) -> Judged:
    """`rows`: the 945's. `shipment_id`: its own stored shipment, when it has one (a press). `for_update`: the lines are locked
    (before the figures are read) — the caller is going to book it."""
    q = select(OrderLine).where(OrderLine.order_id == order.id, OrderLine.removed.is_(False)).order_by(OrderLine.line_no)
    if for_update:
        q = q.with_for_update().execution_options(populate_existing=True)
    every = list((await s.execute(q)).scalars())
    items = {i.item_code: i for i in (await s.execute(select(Item).where(
        Item.item_code.in_([ln.item_code for ln in every if ln.item_code])))).scalars()}
    sent = await latest_940(s, order.id)
    locked = (((sent.body_json or {}).get("locked") or {}).get("lines") or {}) if sent is not None and sent.sent_at is not None else {}
    lines = [ln for ln in every if str(ln.line_no) in locked and items.get(ln.item_code) and items[ln.item_code].warehouse_held]
    carried = {ln.id: int(locked[str(ln.line_no)].get("qty_ea") or 0) for ln in lines}

    def ids_of(ln: OrderLine) -> set[str]:
        return line_identifiers(ln.item_code, ln.source, items[ln.item_code].upc, items[ln.item_code].gtin)

    groups, unmatched = match_rows(rows, lines, ids_of, lambda ln: max(carried[ln.id] - (ln.shipped_ea or 0), 0))
    figures = tuple(ItemFigures(g.lines[0].item_code, sum(carried[ln.id] for ln in g.lines),
                                sum(ln.shipped_ea or 0 for ln in g.lines), g.total) for g in groups)
    mine = {ln.id: ln.item_code for g in groups for ln in g.lines}
    facts = Facts(
        source_type=order.source_type, fulfillment=order.fulfillment_status, held_from=order.fulfillment_status_before_hold,
        cancelled_at_source=order.source_cancelled_at is not None, historical=bool(order.historical), shadow=bool(order.shadow),
        own_940=bool(locked), invoice=order.invoice_status, invoiced=await _invoiced(s, order.id),
        child=(await s.execute(select(Order.id).where(Order.parent_order_id == order.id).limit(1))).first() is not None,
        returned=tuple(sorted(await _returned(s, order.id, every, mine))), adjusted=tuple(sorted(await _adjusted(s, order.id, set(mine.values())))),
        unmatched=tuple(f"{r.item_code or r.upc or '?'} {r.qty} EA" for r in unmatched),
        waiting=tuple(n for x, n in await waiting_945s(s, order.id) if x.id != shipment_id) if by == PRESS else (),
        items=figures)
    return Judged(answer(facts, by=by), lines, carried, groups)


async def _invoiced(s, order_id: int) -> bool:
    for model in (Invoice, DeliveryConfirmation):
        if (await s.execute(select(model.id).where(model.order_id == order_id).limit(1))).first() is not None:
            return True
    return False


async def _returned(s, order_id: int, every: Sequence[OrderLine], mine: dict[int, str]) -> set[str]:
    """This 945's items with a refund line or a recorded return. A refund line whose order line is not known counts for all."""
    refunded = (await s.execute(select(OrderRefundLine.order_line_id).join(OrderRefund, OrderRefund.id == OrderRefundLine.refund_id)
                                .where(OrderRefund.order_id == order_id))).scalars()
    back = {lid for lid in refunded} | {ln.id for ln in every if ln.returned_ea}
    for recorded in (await s.execute(select(OrderReturn.lines).where(OrderReturn.order_id == order_id))).scalars():
        back |= {int(x["order_line_id"]) for x in recorded}
    return set(mine.values()) if None in back else {mine[lid] for lid in back if lid in mine}


async def _adjusted(s, order_id: int, codes: set[str]) -> set[str]:
    """Of these items, those a person took out of Radiant's stock since the first 945 (any lot, or none)."""
    if not codes:
        return set()
    first = (await s.execute(select(func.min(Shipment.ship_date)).where(Shipment.order_id == order_id, Shipment.applied.is_(True)))).scalar()
    since = [StockMove.at >= clock().at(first, time(0))] if first is not None else []
    return set((await s.execute(select(StockMove.item_code).where(StockMove.source_type == "adjustment", StockMove.from_place == RADIANT,
                                                                  StockMove.item_code.in_(codes), *since))).scalars())


def open_in_shopify(order: Order) -> bool:
    """Lines of it are still open in Shopify, by what Sales last read of it: nothing is written for a later part, so a person
    closes them there."""
    return order.source_type == "SHOPIFY" and order.shopify_fulfillment_status != "fulfilled"


async def book(uow, tasks, order: Order, j: Judged, shipment: Shipment, rows: Sequence[ShipmentRow], name: str) -> None:
    """The 945 counts: its quantities on the lines, its rows on their lines, and the short-shipment task as it now stands. The
    lines, the shipment and the rows are locked."""
    s = uow.session
    by_id = {ln.id: ln for ln in j.lines}
    short = j.short  # before the lines move
    for g in j.groups:
        for lid, n in g.shipped.items():
            if n:
                by_id[lid].shipped_ea = (by_id[lid].shipped_ea or 0) + n
    links = j.links
    for r in rows:
        r.order_line_id = links[(r.lx_index, r.w12_index)]
    shipment.applied = True
    open_there = (" In Shopify lines of this order are still open: nothing is written there for a later 945, so fulfil or close "
                  "them there by hand." if open_in_shopify(order) else "")
    if short:
        await tasks.raise_problem(s, SHORT_SHIPMENT, order.id, problem(
            SHORT_SHIPMENT, f"Short shipment: order {order.label}",
            f"Order {order.label}: " + "; ".join(short) + f" after {name}, a later 945 of it. Decide whether to send the rest." + open_there))
    elif open_there:
        await tasks.raise_problem(s, SHORT_SHIPMENT, order.id, problem(
            SHORT_SHIPMENT, f"Shipped in full, open in Shopify: order {order.label}",
            f"Order {order.label}: {name}, a later 945, brought the rest.{open_there}"))
    else:
        await tasks.resolve_problem(s, SHORT_SHIPMENT, order.id, by=uow.actor.user_id)
    over = j.answer.over
    what = ", ".join(f"{i.item_code} {i.this}" for i in j.answer.items)
    await oplog.write(s, "INFO", STEP, f"945 {name}: {what} EA added to order {order.label} as "
                      + (f"more than ordered ({above_words(over)})" if over else "the part its first 945 left")
                      + "; nothing is written to Shopify", document_id=shipment.document_id, order_id=order.id,
                      context={"shipment_id": shipment.id, "short": short})


async def waiting_for_press(s, order: Order) -> tuple[Shipment, str, Judged] | None:
    """The 945 a person decides about in Compare the 945s, its file's name and the rule's answer. The newest, when several wait
    (the answer then refuses, naming the others)."""
    if order.fulfillment_status != F.ON_HOLD or order.fulfillment_status_before_hold != F.SHIPPED:
        return None
    waiting = await waiting_945s(s, order.id)
    if not waiting:
        return None
    sh, name = waiting[-1]
    return sh, name, await judge(s, order, as_rows(await stored_rows(s, sh.id)), by=PRESS, shipment_id=sh.id)


async def press(uow, tasks, order: Order, shipment_id: int, kind: str, reason: str) -> None:
    """A person adds the 945 that waits (`kind`: what the page said it is). The order is locked; its version was checked."""
    s = uow.session
    found = await waiting_for_press(s, order)
    if found is None or found[0].id != shipment_id:
        raise BusinessError("order.later_945", "that 945 is not the one waiting on this order any more: reload it")
    sh, name, _ = found
    # Lines, then the shipment, then its rows (the order is locked): read again under their locks, so what is booked is what is judged.
    await s.execute(select(OrderLine.id).where(OrderLine.order_id == order.id).order_by(OrderLine.id).with_for_update())
    sh = (await s.execute(select(Shipment).where(Shipment.id == sh.id).with_for_update().execution_options(populate_existing=True))).scalar_one()
    rows = await stored_rows(s, sh.id, for_update=True)
    j = await judge(s, order, as_rows(rows), by=PRESS, shipment_id=sh.id, for_update=True)
    if j.answer.kind != kind:
        raise BusinessError("order.later_945", j.answer.refused or "this 945 is not what it was: reload the order")
    over = j.answer.over
    words = "more than ordered: " + above_words(over) if over else "the part the first 945 left"
    uow.set_reason(f"945 {name} applied ({words}): {reason}")
    stopped = await applied_945s(s, order.id)  # before this one counts
    await book(uow, tasks, order, j, sh, rows, name)
    sh.checks = [*(sh.checks or []), {"check": "applied by hand", "message": f"{words}: {reason}"}]
    FULFILLMENT.transition(order, F.SHIPPED, actor=uow.actor, events=uow.events, reason=reason)
    from app.services.shipments import queue_stopped  # it imports this module
    queue_stopped(s, order, stopped)
    by = uow.actor.user_id
    await tasks.resolve_problem(s, SECOND_945, order.id, by=by)
    await tasks.resolve_problem(s, EDI_945_VALIDATION, sh.id, by=by)
    await s.flush()


__all__ = ["Judged", "applied_945s", "as_rows", "book", "judge", "later_ones", "open_in_shopify", "press", "shipment_content",
           "stored_content", "stored_rows", "waiting_945s", "waiting_for_press"]
