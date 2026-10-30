"""Orders as people see them (design §7): the list with the derived order
state, the detail with lines, source documents and the timeline, the review
worklist's "re-check", and manual orders (FR-OB-19a)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select

from app.domain.order_rules import order_state, payment_satisfied, to_ea
from app.domain.status import FULFILLMENT, Fulfillment as F
from app.domain.status import Invoice as Inv
from app.models.config import Connection, Item, Partner
from app.models.documents import Document, DocumentLink
from app.models.orders import Order, OrderLine
from app.models.records import AuditRecord
from app.platform.authorization import requires
from app.platform.errors import BusinessError, InvalidInput
from app.platform.repository import FilterRule, Repository, parse_bool
from app.platform.services import ReadOnlyService
from app.schemas.orders import (
    LinePreview, LineRead, ManualLineIn, ManualOrderIn, OrderDetail, OrderDocumentRead, OrderRead, ReviewResult,
    TimelineEntry,
)
from app.services.intake import ConnectionSnapshot
from app.services.orders import BarcodeLookup, OrderSync

READ = "sales.read"
OPERATE = "sales.order.operate"


class OrderRepository(Repository[Order]):
    model = Order
    search_fields = ("source_order_number", "source_reference", "source_order_id", "review_reason")
    sortable = {"order_number": Order.order_number, "first_received_at": Order.first_received_at,
                "order_date": Order.order_date, "partner_code": Order.partner_code,
                "fulfillment_status": Order.fulfillment_status}
    filterable = {
        "fulfillment_status": FilterRule(Order.fulfillment_status), "partner_code": FilterRule(Order.partner_code),
        "source_type": FilterRule(Order.source_type),
        "historical": FilterRule(Order.historical, ops=frozenset({"eq"}), cast=parse_bool),
    }
    default_sort = ("-order_number",)
    historical_column = "historical"


class OrderService(ReadOnlyService[Order, OrderRead]):
    repo_cls = OrderRepository
    read_schema = OrderRead
    read_permission = READ

    def to_read(self, o: Order) -> OrderRead:
        out = OrderRead.model_validate(o, from_attributes=True)
        out.order_state = order_state(o.fulfillment_status, o.invoice_status)
        return out

    @requires(READ)
    async def detail(self, id: int) -> OrderDetail:
        s = self.uow.session
        o = await self.repo.get(id)
        lines = list((await s.execute(select(OrderLine).where(OrderLine.order_id == id).order_by(OrderLine.line_no))).scalars())
        docs = (await s.execute(select(Document.id, Document.kind, Document.remote_name, Document.created_at)
                                .join(DocumentLink, DocumentLink.document_id == Document.id)
                                .where(DocumentLink.entity_type == "order", DocumentLink.entity_id == id)
                                .order_by(Document.id))).all()
        line_ids = [str(ln.id) for ln in lines]
        trail = (await s.execute(select(AuditRecord).where(or_(
            (AuditRecord.entity == "sales_orders") & (AuditRecord.entity_id == str(id)),
            (AuditRecord.entity == "sales_order_lines") & (AuditRecord.entity_id.in_(line_ids) if line_ids else False),
        )).order_by(AuditRecord.at.desc(), AuditRecord.id.desc()).limit(200))).scalars()
        base = self.to_read(o).model_dump()
        return OrderDetail(
            **base, **{k: getattr(o, k) for k in ("bill_to", "contact", "notes", "subtotal", "tax", "shipping",
                                                   "discount", "department", "order_type")},
            lines=[LineRead.model_validate(ln) for ln in lines],
            documents=[OrderDocumentRead(id=d.id, kind=d.kind, remote_name=d.remote_name, created_at=d.created_at) for d in docs],
            timeline=[TimelineEntry(at=a.at, user_name=a.user_name, entity=a.entity, action=a.action, before=a.before,
                                    after=a.after, reason=a.reason) for a in trail],
        )

    @requires(OPERATE)
    async def resolve_review(self, id: int, tasks, shopify=None) -> ReviewResult:
        o = await self.repo.get(id, for_update=True)
        if o.fulfillment_status != F.REVIEW:
            raise BusinessError("order.not_in_review", "the order is not in review",
                                {"status": o.fulfillment_status})
        self.uow.set_reason("re-check review")
        remaining = await OrderSync(self.uow, tasks).recheck(o, await self._barcodes(o, shopify))
        return ReviewResult(order=self.to_read(o), remaining=remaining)


    async def _barcodes(self, o: Order, shopify) -> BarcodeLookup | None:
        """The store the order came from: the active SHOPIFY connection of its
        partner (one per partner in practice; the first by id otherwise)."""
        if o.source_type != "SHOPIFY" or shopify is None:
            return None
        conn = (await self.uow.session.execute(select(Connection).where(
            Connection.kind == "SHOPIFY", Connection.partner_code == o.partner_code, Connection.active.is_(True))
            .order_by(Connection.id).limit(1))).scalar_one_or_none()
        return BarcodeLookup(shopify, ConnectionSnapshot.of(conn)) if conn else None


class ManualOrderService:
    """FR-OB-19a. Draft is never eligible; Submit makes it Pending and it
    follows the same flows as any other order. Every step is audited."""

    def __init__(self, uow) -> None:
        self.uow = uow
        self.s = uow.session

    async def _partner(self, code: str) -> Partner:
        p = (await self.s.execute(select(Partner).where(Partner.code == code))).scalar_one_or_none()
        if p is None or not p.active:
            raise InvalidInput("partner.unknown", f"no active partner {code!r}", {"field": "partner_code"})
        if p.channel != "MANUAL":
            raise InvalidInput("order.manual_partner", "manual orders are for a MANUAL-channel partner",
                               {"field": "partner_code"})
        return p

    async def _preview(self, lines: list[ManualLineIn]) -> list[LinePreview]:
        codes = {ln.item_code for ln in lines}
        items = {i.item_code: i for i in (await self.s.execute(
            select(Item).where(Item.item_code.in_(codes), Item.active.is_(True)))).scalars()}
        out = []
        for ln in lines:
            item = items.get(ln.item_code)
            if item is None:
                out.append(LinePreview(item_code=ln.item_code, quantity=ln.quantity, unit=ln.unit, qty_ea=None,
                                       problem="item not in the Item Master"))
                continue
            qty_ea, why = to_ea(ln.quantity, ln.unit, {u.unit: Decimal(u.factor_ea) for u in item.units if u.active})
            out.append(LinePreview(item_code=ln.item_code, quantity=ln.quantity, unit=ln.unit, qty_ea=qty_ea, problem=why))
        return out

    @requires(OPERATE)
    async def preview(self, lines: list[ManualLineIn]) -> list[LinePreview]:
        """The entry screen's EA preview, before saving."""
        return await self._preview(lines)

    def _lines(self, order: Order, previews: list[LinePreview]) -> list[OrderLine]:
        return [OrderLine(order_id=order.id, line_no=i, item_code=p.item_code if p.problem is None else None,
                          source={"item_code": p.item_code}, ordered_qty=p.quantity, order_unit=p.unit,
                          factor_ea=(Decimal(p.qty_ea) / p.quantity) if p.qty_ea else None, qty_ea=p.qty_ea,
                          problem=p.problem)
                for i, p in enumerate(previews, start=1)]

    @requires(OPERATE)
    async def create(self, data: ManualOrderIn) -> Order:
        p = await self._partner(data.partner_code)
        self.uow.set_reason("manual order: create draft")
        order = Order(partner_code=p.code, source_type="MANUAL", source_order_id=f"MANUAL-{uuid.uuid4().hex}",
                      source_order_number=data.customer_reference, requested_delivery_date=data.requested_delivery_date,
                      ship_to=data.ship_to, notes=data.notes, fulfillment_status=F.DRAFT.value,
                      invoice_status=(Inv.NOT_REQUIRED if p.invoice_method == "NONE" else Inv.PENDING).value,
                      first_received_at=datetime.now(timezone.utc), version=1)
        self.s.add(order)
        await self.s.flush()
        order.source_reference = f"{p.abbrev or p.code}-M{order.order_number}"
        for ln in self._lines(order, await self._preview(data.lines)):
            self.s.add(ln)
        await self.s.flush()
        return order

    async def _draft(self, id: int, version: int | None) -> Order:
        o = (await self.s.execute(select(Order).where(Order.id == id).with_for_update())).scalar_one_or_none()
        if o is None or o.source_type != "MANUAL":
            raise BusinessError("entity.not_found", f"manual order {id} not found", status_code=404)
        if o.fulfillment_status != F.DRAFT:
            raise BusinessError("order.not_draft", "only a Draft can be changed or submitted",
                                {"status": o.fulfillment_status})
        if version is not None and o.version != version:
            raise BusinessError("entity.stale", "this order was changed by someone else; reload it",
                                {"current_version": o.version, "sent_version": version})
        return o

    @requires(OPERATE)
    async def update(self, id: int, data: ManualOrderIn, version: int | None) -> Order:
        o = await self._draft(id, version)
        p = await self._partner(data.partner_code)
        self.uow.set_reason("manual order: edit draft")
        o.partner_code, o.source_order_number = p.code, data.customer_reference
        o.requested_delivery_date, o.ship_to, o.notes = data.requested_delivery_date, data.ship_to, data.notes
        for ln in (await self.s.execute(select(OrderLine).where(OrderLine.order_id == o.id))).scalars():
            await self.s.delete(ln)
        await self.s.flush()
        for ln in self._lines(o, await self._preview(data.lines)):
            self.s.add(ln)
        await self.s.flush()
        return o

    @requires(OPERATE)
    async def submit(self, id: int, version: int | None) -> Order:
        o = await self._draft(id, version)
        bad = list((await self.s.execute(select(OrderLine.line_no, OrderLine.problem).where(
            OrderLine.order_id == o.id, OrderLine.problem.is_not(None)))).all())
        if bad:
            raise InvalidInput("order.lines_invalid", "fix every line before submitting",
                               {"field": "lines", "lines": [{"line_no": n, "problem": p} for n, p in bad]})
        p = await self._partner(o.partner_code)
        self.uow.set_reason("manual order: submit")
        now = datetime.now(timezone.utc)
        o.first_received_at = now  # the delay runs from submission, not from when the draft was started
        if payment_satisfied(p.settlement_mode, "MANUAL", None) and p.settlement_mode == "PREPAID":
            o.paid_at = now
        FULFILLMENT.transition(o, F.PENDING, actor=self.uow.actor, events=self.uow.events, reason="manual order submitted")
        await self.s.flush()
        return o

    @requires(OPERATE)
    async def cancel(self, id: int, version: int | None) -> Order:
        o = await self._draft(id, version)
        self.uow.set_reason("manual order: cancel draft")
        FULFILLMENT.transition(o, F.CANCELLED, actor=self.uow.actor, events=self.uow.events, reason="draft cancelled")
        await self.s.flush()
        return o

