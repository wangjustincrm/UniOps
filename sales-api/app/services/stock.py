"""The Radiant inventory (design §15): the move ledger kept
from the records Sales already has, and what people read and do with it.

`sync` writes the moves that are missing — nothing else ever writes one, except a person's
adjustment. Each move has one source (a transfer line, a 944 row, a 945 row, a delivery, an
adjustment) and that source moves once (unique source_type + source_key): running it again, or
after a file is read again, adds nothing. From the first data Sales holds (user 2026-11-12: no
opening balance; the result is compared with Radiant's own figures).

  transfer sent (943 put, or a transfer known only from its 944) → PLANT → TO_RADIANT, per item as its lines are now
     (amended, or corrected to what arrived: the difference goes back); a difference accepted → TO_RADIANT ↔ ADJUSTED
  944 row of an applied receipt                                 → TO_RADIANT → RADIANT, with its lot
  944 of a return (RA) kept as a file                           → RETURNING → RADIANT, with its lot
     (its order known — RA CRM<Shopify no.>: first CUSTOMER → RETURNING)
  945 row of an applied shipment                                → RADIANT → TO_CUSTOMER, lot and expiry
  945 row of no order (the warehouse shipped it)                → RADIANT → CUSTOMER
  an order shipped with no 945 in Sales (older history)         → RADIANT → CUSTOMER, no lot
  the order's delivery is final                                 → TO_CUSTOMER → CUSTOMER (shipped − returned)
     (delivery confirmed and invoiced, an order with no invoice,      and TO_CUSTOMER → RETURNING (returned),
      or an old order)                                                 lot by lot in shipping order
  a person's adjustment (reason)                                → RADIANT ↔ ADJUSTED
  an applied Radiant count (stock_counts.py)                    → RADIANT ↔ ADJUSTED, at its moment, to Radiant's figure

A 944 row carries no expiry: the lot's expiry as its 945s give it."""
from __future__ import annotations

import collections
import logging
import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select

from app.domain.edi944 import parse_944
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.domain.status import Transfer as T
from app.models.config import Item
from app.models.documents import Document, DocumentLink
from app.models.invoices import DeliveryConfirmation
from app.models.orders import Order, OrderLine
from app.models.shipments import Shipment, ShipmentRow
from app.models.stock import (ADJUSTED, CUSTOMER, PLANT, RADIANT, RETURNING, TO_CUSTOMER, TO_RADIANT, StockItem, StockLot,
                              StockMove)
from app.models.transfers import Receipt, ReceiptRow, Transfer, TransferLine
from app.platform.authorization import requires
from app.platform.errors import BusinessError
from app.platform.query import FilterRule
from app.platform.repository import Repository
from app.platform.services import ReadOnlyService
from app.platform.views import Preset
from app.services import oplog
from app.services.outbound import TORONTO
from app.services.returns_944 import RA_ERROR
from app.schemas.stock import StockItemRead, StockLotRead, StockMoveRead

logger = logging.getLogger(__name__)

READ = "sales.read"
ADJUST = "sales.inventory.adjust"
EXPIRING_DAYS = 90
NOT_SENT = {T.DRAFT.value, T.READY_TO_SEND.value, T.GENERATED_943.value, T.FAILED.value, T.CANCELLED.value}
FINAL_INVOICE = {Inv.QUEUED.value, Inv.SENT.value, Inv.NOT_REQUIRED.value}


def _at(d: date | datetime | None, fallback: datetime | None = None) -> datetime:
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    if isinstance(d, date):
        return datetime.combine(d, time(12), tzinfo=TORONTO)
    return fallback or datetime.now(timezone.utc)


class _Writer:
    def __init__(self, s, known: set[tuple[str, str]]):
        self.s, self.known, self.n = s, known, collections.Counter()

    def add(self, source_type: str, key: str, *, at, item_code, qty, frm, to, lot=None, expiry=None, order_id=None,
            transfer_id=None, document_id=None, reason=None) -> None:
        if not item_code or not qty or qty <= 0 or (source_type, key) in self.known:
            return
        self.known.add((source_type, key))
        self.s.add(StockMove(at=at, item_code=item_code, lot=lot or None, expiry=expiry, qty_ea=int(qty), from_place=frm, to_place=to,
                             source_type=source_type, source_key=key, order_id=order_id, transfer_id=transfer_id,
                             document_id=document_id, reason=reason, created_by_name="Sales"))
        self.n[source_type] += 1


async def sync(uow) -> dict:
    """The moves missing from the ledger, written in this unit of work (the caller commits)."""
    s = uow.session
    known = set((await s.execute(select(StockMove.source_type, StockMove.source_key))).all())
    w = _Writer(s, known)
    expiry_of = dict((await s.execute(select(ShipmentRow.lot, func.max(ShipmentRow.expiry)).where(
        ShipmentRow.lot.is_not(None), ShipmentRow.expiry.is_not(None)).group_by(ShipmentRow.lot))).all())

    # Transfers: sent (a 943 put), or known only from their 944 (the old system's). Per transfer and item, what
    # left the plant follows its lines as they are now: amended before its 944, or corrected to what arrived
    # (user 2026-11-13), the difference moves back (or out) — written once per state (key: what was moved before).
    first_receipt = dict((await s.execute(select(Receipt.transfer_id, func.min(Receipt.receipt_date))
                                          .where(Receipt.applied.is_(True)).group_by(Receipt.transfer_id))).all())
    sent_now: dict[tuple[int, str], int] = {}
    tinfo: dict[int, tuple] = {}
    rows = (await s.execute(select(TransferLine.item_code, TransferLine.qty_ea, Transfer, Document.sent_at)
                            .join(Transfer, Transfer.id == TransferLine.transfer_id)
                            .outerjoin(Document, Document.id == Transfer.document_id)
                            .where(Transfer.status.not_in(NOT_SENT)))).all()
    for code, q, t, sent_at in rows:
        sent_now[(t.id, code)] = sent_now.get((t.id, code), 0) + int(q)
        tinfo[t.id] = (_at(sent_at or first_receipt.get(t.id), t.created_at), t.document_id, t.resolution)
    moved: dict[tuple[int, str], int] = {}
    for tid, code, frm, to, q in (await s.execute(select(StockMove.transfer_id, StockMove.item_code, StockMove.from_place,
                                                        StockMove.to_place, StockMove.qty_ea).where(
            StockMove.transfer_id.is_not(None), StockMove.source_type.in_(("transfer_line", "transfer_sent"))))).all():
        k = (tid, code)
        moved[k] = moved.get(k, 0) + (q if (frm, to) == (PLANT, TO_RADIANT) else -q if (frm, to) == (TO_RADIANT, PLANT) else 0)
    for (tid, code) in set(sent_now) | {k for k in moved if k[0] in tinfo}:
        at, doc_id, _ = tinfo[tid]
        diff = sent_now.get((tid, code), 0) - moved.get((tid, code), 0)
        if diff:
            w.add("transfer_sent", f"{tid}:{code}:{moved.get((tid, code), 0)}", at=at, item_code=code, qty=abs(diff),
                  frm=PLANT if diff > 0 else TO_RADIANT, to=TO_RADIANT if diff > 0 else PLANT, transfer_id=tid, document_id=doc_id)

    # 944 rows of applied receipts.
    rows = (await s.execute(select(ReceiptRow, Receipt).join(Receipt, Receipt.id == ReceiptRow.receipt_id)
                            .where(Receipt.applied.is_(True), Receipt.transfer_id.is_not(None)))).all()
    for r, rec in rows:
        w.add("receipt_row", str(r.id), at=_at(rec.receipt_date, rec.created_at), item_code=r.item_code, qty=r.qty_ea, frm=TO_RADIANT,
              to=RADIANT, lot=r.lot, expiry=expiry_of.get(r.lot), transfer_id=rec.transfer_id, document_id=rec.document_id)

    # A receipt difference accepted as real (user 2026-11-13): what stays in transit for that transfer is written off.
    got: dict[tuple[int, str], int] = {}
    for tid, code, q in (await s.execute(select(Receipt.transfer_id, ReceiptRow.item_code, ReceiptRow.qty_ea).join(
            Receipt, Receipt.id == ReceiptRow.receipt_id).where(Receipt.applied.is_(True), Receipt.transfer_id.is_not(None)))).all():
        got[(tid, code)] = got.get((tid, code), 0) + int(q)
    for tid, (at, doc_id, resolution) in tinfo.items():
        if resolution != "accepted":
            continue
        for code in {c for (t2, c) in set(sent_now) | set(got) if t2 == tid}:
            rest = sent_now.get((tid, code), 0) - got.get((tid, code), 0)
            if rest:
                w.add("transfer_accepted", f"{tid}:{code}", at=at, item_code=code, qty=abs(rest),
                      frm=TO_RADIANT if rest > 0 else ADJUSTED, to=ADJUSTED if rest > 0 else TO_RADIANT, transfer_id=tid,
                      document_id=doc_id, reason="receipt difference accepted (lost / extra in transit)")

    # Returns received at Radiant (RA 944s, kept as files).
    docs = (await s.execute(select(Document.id, Document.created_at).where(
        Document.kind == "944_EDI", Document.error.like(f"{RA_ERROR}%")))).all()
    ra_order = dict((await s.execute(select(DocumentLink.document_id, DocumentLink.entity_id).where(
        DocumentLink.entity_type == "order", DocumentLink.document_id.in_([d for d, _ in docs])))).all()) if docs else {}
    for did, created in docs:
        oid = ra_order.get(did)
        done_ra = any(k[0] == "return_944" and k[1].startswith(f"{did}:") for k in known)
        done_order = oid is None or any(k[0] == "return_order" and k[1].startswith(f"{did}:") for k in known)
        if done_ra and done_order:
            continue
        content = (await s.execute(select(Document.content).where(Document.id == did))).scalar_one()
        try:
            r = parse_944(bytes(content))
        except Exception as e:  # noqa: BLE001 — one unreadable file never stops the ledger
            logger.warning("RA 944 %s not read: %s", did, type(e).__name__)
            continue
        for i, row in enumerate(r.rows, start=1):
            at = _at(r.receipt_date, created)
            if oid is not None:  # its order is known: it came back from that customer (RA CRM<Shopify no.>)
                w.add("return_order", f"{did}:{i}", at=at, item_code=row.item_code, qty=row.qty, frm=CUSTOMER, to=RETURNING,
                      lot=row.lot, expiry=expiry_of.get(row.lot), order_id=oid, document_id=did)
            w.add("return_944", f"{did}:{i}", at=at, item_code=row.item_code, qty=row.qty, frm=RETURNING,
                  to=RADIANT, lot=row.lot, expiry=expiry_of.get(row.lot), order_id=oid, document_id=did)

    # 945 rows: what left Radiant.
    rows = (await s.execute(select(ShipmentRow, Shipment).join(Shipment, Shipment.id == ShipmentRow.shipment_id)
                            .where(((Shipment.applied.is_(True)) | (Shipment.order_id.is_(None) & Shipment.shadow.is_(False)))))).all()
    by_line: dict[int | None, list] = collections.defaultdict(list)
    orders: dict[int, list] = collections.defaultdict(list)
    for r, sh in rows:
        no_order = sh.order_id is None
        w.add("shipment_row", str(r.id), at=_at(sh.ship_date, sh.created_at), item_code=r.item_code, qty=r.qty_shipped_ea,
              frm=RADIANT, to=CUSTOMER if no_order else TO_CUSTOMER, lot=r.lot, expiry=r.expiry or expiry_of.get(r.lot),
              order_id=sh.order_id, document_id=sh.document_id)
        if not no_order:
            orders[sh.order_id].append((r, sh))

    # Orders shipped with no 945 in Sales (Shopify's history before Sales read 945s): out of Radiant at the
    # order date, no lot — only from Radiant's first receipt on (before it, orders shipped from elsewhere).
    start = (await s.execute(select(func.min(Receipt.receipt_date)).where(Receipt.applied.is_(True)))).scalar()
    if start is not None:
        has_945 = select(Shipment.id).where(Shipment.order_id == Order.id, Shipment.applied.is_(True)).exists()
        rows = (await s.execute(select(OrderLine.id, OrderLine.item_code, OrderLine.qty_ea, Order.id, Order.order_date)
                                .join(Order, Order.id == OrderLine.order_id)
                                .where(Order.fulfillment_status == F.SHIPPED.value, ~has_945, Order.order_date >= start,
                                       OrderLine.removed.is_(False), OrderLine.qty_ea.is_not(None)))).all()
        for line_id, code, qty, oid, odate in rows:
            w.add("order_line", str(line_id), at=_at(odate), item_code=code, qty=qty, frm=RADIANT, to=CUSTOMER, order_id=oid)

    # Deliveries: final when invoiced (or nothing to invoice) or the order is old.
    if orders:
        final = (await s.execute(select(Order.id, Order.invoice_status, Order.historical).where(Order.id.in_(list(orders))))).all()
        confirmed = dict((await s.execute(select(DeliveryConfirmation.order_id, DeliveryConfirmation.confirmed_date)
                                          .where(DeliveryConfirmation.order_id.in_(list(orders))))).all())
        returned = dict((await s.execute(select(OrderLine.id, OrderLine.returned_ea).where(OrderLine.order_id.in_(list(orders))))).all())
        for oid, inv, historical in final:
            if not (historical or inv in FINAL_INVOICE):
                continue
            by_line = collections.defaultdict(list)
            for r, sh in orders[oid]:
                by_line[r.order_line_id].append((r, sh))
            for line_id, rs in by_line.items():
                back = int(returned.get(line_id) or 0) if line_id else 0
                total = sum(r.qty_shipped_ea or 0 for r, _ in rs)
                keep = max(total - back, 0)  # delivered, lot by lot in shipping order; the rest came back
                if any(("delivery", str(r.id)) in known for r, _ in rs):
                    continue  # its delivery is in the ledger: a return found later comes as its RA 944 (return_order)
                for r, sh in sorted(rs, key=lambda x: (x[1].ship_date or date.min, x[0].id)):
                    q = r.qty_shipped_ea or 0
                    d = min(q, keep)
                    keep -= d
                    at = _at(confirmed.get(oid) or sh.ship_date, sh.created_at)
                    kw = dict(at=at, item_code=r.item_code, lot=r.lot, expiry=r.expiry or expiry_of.get(r.lot), order_id=oid,
                              document_id=sh.document_id)
                    w.add("delivery", str(r.id), qty=d, frm=TO_CUSTOMER, to=CUSTOMER, **kw)
                    w.add("returned", str(r.id), qty=q - d, frm=TO_CUSTOMER, to=RETURNING, **kw)
    await s.flush()
    # Applied Radiant counts last: at each count's moment every lot holds Radiant's figure (a late 945 dated
    # before it changes the count's move, not the figure).
    from app.services.stock_counts import count_moves
    if n := await count_moves(s):
        w.n["count"] += n
    return dict(w.n)


async def sync_all(uow_factory) -> dict:
    from app.platform.actors import SYSTEM_ACTOR
    async with uow_factory(SYSTEM_ACTOR) as u:
        out = await sync(u)
        if out:
            await oplog.write(u.session, "INFO", "stock", f"inventory: {sum(out.values())} moves added", context=out)
        await u.commit()
    return out


# ── reading and adjusting ────────────────────────────────────────────────────

class StockItemRepository(Repository[StockItem]):
    model = StockItem
    search_fields = ("item_code", "description")
    list_fields = ("item_code", "description", "received_ea", "returned_ea", "shipped_ea", "adjusted_ea", "on_hand_ea", "hold_ea",
                   "allocated_ea", "available_ea", "to_radiant_ea", "to_customer_ea", "returning_ea", "lots", "first_expiry",
                   "days_to_first_expiry", "last_move_at")
    labels = {"item_code": "Item", "received_ea": "Received EA", "returned_ea": "Returned EA", "shipped_ea": "Shipped EA",
              "adjusted_ea": "Adjusted EA", "on_hand_ea": "On hand EA", "hold_ea": "On hold EA", "allocated_ea": "Allocated EA",
              "available_ea": "Available EA", "to_radiant_ea": "In transit to Radiant EA", "to_customer_ea": "In transit to customers EA",
              "returning_ea": "Returns on the way EA", "lots": "Lots on hand", "first_expiry": "First expiry",
              "days_to_first_expiry": "Days to first expiry", "last_move_at": "Last move"}
    suggestable = ("item_code",)
    summable = ("received_ea", "returned_ea", "shipped_ea", "adjusted_ea", "on_hand_ea", "hold_ea", "allocated_ea", "available_ea",
                "to_radiant_ea", "to_customer_ea", "returning_ea")
    default_sort = ("item_code",)


class StockLotRepository(Repository[StockLot]):
    model = StockLot
    search_fields = ("item_code", "lot")
    list_fields = ("item_code", "lot", "expiry", "days_to_expiry", "received_ea", "returned_ea", "shipped_ea", "adjusted_ea",
                   "on_hand_ea", "hold_ea", "available_ea", "to_customer_ea", "returning_ea", "to_radiant_ea", "first_move_at", "last_move_at")
    filterable = {"item_code": FilterRule(StockLot.item_code)}
    labels = {"item_code": "Item", "received_ea": "Received EA", "returned_ea": "Returned EA", "shipped_ea": "Shipped EA",
              "adjusted_ea": "Adjusted EA", "on_hand_ea": "On hand EA", "hold_ea": "On hold EA", "available_ea": "Available EA",
              "to_customer_ea": "In transit to customers EA", "returning_ea": "Returns on the way EA",
              "days_to_expiry": "Days to expiry", "first_move_at": "First move", "last_move_at": "Last move"}
    suggestable = ("item_code", "lot")
    groupable = ("item_code", "expiry")
    summable = ("received_ea", "returned_ea", "shipped_ea", "adjusted_ea", "on_hand_ea", "hold_ea", "available_ea", "to_customer_ea", "returning_ea")
    default_sort = ("item_code", "expiry", "lot")
    presets = (
        Preset("on_hand", "On hand", {"filters": [{"field": "on_hand_ea", "op": "gt", "value": 0}]}),
        Preset("expiring", f"Expiring soon ({EXPIRING_DAYS} days)", {
            "filters": [{"field": "on_hand_ea", "op": "gt", "value": 0}, {"field": "days_to_expiry", "op": "between", "value": [0, EXPIRING_DAYS]}],
            "sort": [{"field": "expiry", "desc": False}]}),
        Preset("negative", "Below zero (check)", {"filters": [{"field": "on_hand_ea", "op": "lt", "value": 0}]}),
        Preset("all", "All lots"),
    )


class StockMoveRepository(Repository[StockMove]):
    model = StockMove
    search_fields = ("item_code", "lot", "reason")
    list_fields = ("at", "item_code", "lot", "expiry", "qty_ea", "from_place", "to_place", "source_type", "order_id", "transfer_id",
                   "document_id", "reason", "created_by_name")
    filterable = {"item_code": FilterRule(StockMove.item_code), "lot": FilterRule(StockMove.lot),
                  "source_type": FilterRule(StockMove.source_type)}
    labels = {"at": "When", "item_code": "Item", "qty_ea": "Qty EA", "from_place": "From", "to_place": "To", "source_type": "Source",
              "order_id": "Order", "transfer_id": "Transfer", "document_id": "File", "created_by_name": "By"}
    suggestable = ("item_code", "lot")
    groupable = ("item_code", "lot", "source_type", "from_place", "to_place")
    summable = ("qty_ea",)
    default_sort = ("-at",)


class StockItemService(ReadOnlyService[StockItem, StockItemRead]):
    repo_cls = StockItemRepository
    read_schema = StockItemRead
    read_permission = READ


class StockLotService(ReadOnlyService[StockLot, StockLotRead]):
    repo_cls = StockLotRepository
    read_schema = StockLotRead
    read_permission = READ


class StockMoveService(ReadOnlyService[StockMove, StockMoveRead]):
    repo_cls = StockMoveRepository
    read_schema = StockMoveRead
    read_permission = READ

    @requires(ADJUST)
    async def adjust(self, *, item_code: str, lot: str | None, qty_ea: int, reason: str) -> StockMove:
        """A person's correction at Radiant: + found / counted more, − written off (damaged, destroyed, counted less)."""
        s = self.uow.session
        if qty_ea == 0:
            raise BusinessError("stock.zero", "the quantity must not be zero")
        if (await s.execute(select(Item.id).where(Item.item_code == item_code))).first() is None:
            raise BusinessError("stock.item", f"no item {item_code}", status_code=404)
        expiry = (await s.execute(select(func.max(StockMove.expiry)).where(StockMove.item_code == item_code, StockMove.lot == lot))).scalar()
        actor = self.uow.actor
        m = StockMove(at=datetime.now(timezone.utc), item_code=item_code, lot=lot or None, expiry=expiry, qty_ea=abs(qty_ea),
                      from_place=ADJUSTED if qty_ea > 0 else RADIANT, to_place=RADIANT if qty_ea > 0 else ADJUSTED,
                      source_type="adjustment", source_key=uuid.uuid4().hex, reason=reason,
                      created_by=getattr(actor, "user_id", None), created_by_name=getattr(actor, "name", None))
        s.add(m)
        self.uow.set_reason(f"inventory adjustment: {reason}")
        await s.flush()
        await oplog.write(s, "INFO", "stock", f"inventory adjusted: {item_code} {lot or '(no lot)'} {qty_ea:+d} EA — {reason}")
        return m
