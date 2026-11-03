"""Orders as people see them (design §7): the list with the derived order
state, the detail with lines, source documents and the timeline, the review
worklist's "re-check", and manual orders (FR-OB-19a)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, case, or_, select

from app.domain.address import address_flags
from app.domain.order_rules import ON_HOLD_FULFILLMENT, order_state, payment_satisfied, to_ea
from app.domain.orders import HOLD_REASONS, ORDER_STATES, SOURCE_TYPES
from app.domain.status import FULFILLMENT, Fulfillment as F
from app.domain.status import Invoice as Inv
from app.models.config import Connection, DcRouting, Item, Partner
from app.models.documents import Document, DocumentLink
from app.models.shipments import Shipment, ShipmentRow
from app.models.orders import Order, OrderLine
from app.models.records import AuditRecord
from app.platform.authorization import requires
from app.platform.errors import BusinessError, InvalidInput
from app.platform.query import FilterRule
from app.platform.repository import Repository
from app.platform.services import ReadOnlyService
from app.platform.views import Preset
from app.schemas.orders import (
    LinePreview, LineRead, ManualLineIn, ManualOrderIn, OrderDetail, OrderDocumentRead, OrderRead, ReviewResult,
    ShipmentRead, ShipmentRowRead, TimelineEntry,
)
from app.services.intake import ConnectionSnapshot
from app.services.orders import BarcodeLookup, OrderSync, order_ref
from app.services.settings import SETTINGS, SHADOW_MODE

READ = "sales.read"
_DOC_COLUMNS = (Document.id, Document.kind, Document.direction, Document.remote_name, Document.created_at,
                Document.file_status, Document.ack_status, Document.st02, Document.sent_at, Document.error,
                Document.ack_detail)
OPERATE = "sales.order.operate"


# The derived order state (D17) in SQL: the same rule as order_rules.order_state
# (a test holds them together), so the list filters and the dashboard counts on it.
ORDER_STATE_SQL = case(
    (Order.fulfillment_status == F.DRAFT.value, "Draft"),
    (Order.fulfillment_status == F.CANCELLED.value, "Cancelled"),
    (or_(Order.fulfillment_status.in_([s.value for s in ON_HOLD_FULFILLMENT]),
         Order.invoice_status.in_([Inv.BLOCKED.value, Inv.FAILED.value])), "On Hold"),
    (and_(Order.fulfillment_status == F.SHIPPED.value, Order.invoice_status.in_([Inv.SENT.value, Inv.NOT_REQUIRED.value])),
     "Completed"),
    else_="Active",
)


class OrderRepository(Repository[Order]):
    model = Order
    search_fields = ("source_reference", "source_order_number", "source_order_id", "partner_name_sql", "review_reason")
    list_fields = ("order_number", "partner_code", "connection_id", "source_type", "source_order_id",
                   "source_order_number", "source_reference", "order_type", "department", "order_date",
                   "requested_delivery_date", "do_not_deliver_after", "cancel_date", "currency", "subtotal", "tax",
                   "shipping", "discount", "total", "fulfillment_status", "invoice_status", "hold_reason",
                   "review_reason", "historical", "first_received_at", "paid_at", "source_updated_at",
                   "source_cancelled_at", "archived_in_shopify_at", "archive_suppressed", "shopify_financial_status",
                   "shopify_fulfillment_status", "created_at", "updated_at")
    labels = {"order_number": "Internal no.", "source_reference": "Order ref", "partner_name": "Partner",
              "partner_code": "Partner code", "source_order_number": "Source no.", "source_order_id": "Source ID",
              "fulfillment_status": "Status", "first_received_at": "Received", "paid_at": "Paid",
              "shopify_financial_status": "Shopify payment", "shopify_fulfillment_status": "Shopify fulfillment"}
    enums = {"source_type": SOURCE_TYPES, "fulfillment_status": tuple(s.value for s in F),
             "invoice_status": tuple(s.value for s in Inv), "hold_reason": HOLD_REASONS,
             "order_state": ORDER_STATES}
    sortable = {"order_state": ORDER_STATE_SQL, "partner_name": Order.partner_name_sql}
    filterable = {"order_state": FilterRule(ORDER_STATE_SQL, kind="text"),
                  "partner_name": FilterRule(Order.partner_name_sql, kind="text")}
    default_sort = ("-order_number",)
    groupable = ("order_state", "partner_name", "partner_code", "source_type", "fulfillment_status", "invoice_status", "hold_reason", "order_date",
                 "currency", "historical", "archive_suppressed", "shopify_financial_status")
    # System views (§13.19). Review reason is shown where it says something.
    presets = (
        Preset("all", "All", {"hidden": ["review_reason"]}),
        Preset("needs_review", "Needs review", {"filters": [{"field": "fulfillment_status", "op": "eq", "value": "Review"}]}),
        Preset("payment_hold", "Payment hold", {"filters": [{"field": "fulfillment_status", "op": "eq", "value": "Payment Hold"}], "hidden": ["review_reason"]}),
        Preset("pending", "Pending (waiting for the 940)", {"filters": [{"field": "fulfillment_status", "op": "eq", "value": "Pending"}], "hidden": ["review_reason"]}),
        Preset("manual_drafts", "Manual drafts", {"filters": [{"field": "source_type", "op": "eq", "value": "MANUAL"},
                                                               {"field": "fulfillment_status", "op": "eq", "value": "Draft"}]}),
        Preset("delivery", "Waiting for delivery confirmation",
               {"filters": [{"field": "invoice_status", "op": "eq", "value": "Waiting for Delivery Confirmation"}]}),
        Preset("archive_suppressed", "Archive suppressed", {"filters": [{"field": "fulfillment_status", "op": "eq", "value": "Archive Suppressed"}], "hidden": ["review_reason"]}),
        Preset("historical", "Historical", {"filters": [{"field": "historical", "op": "eq", "value": "true"}], "includeHistorical": True, "hidden": ["review_reason"]}),
    )
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
        docs = (await s.execute(select(*_DOC_COLUMNS)
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
            documents=[OrderDocumentRead(**d._asdict()) for d in docs],
            shipments=await self._shipments(id),
            dc_number=await self._dc_number(o), ship_to_flags=address_flags(o.ship_to),
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


    @requires(OPERATE)
    async def send_940_now(self, id: int, outbound, *, replace: bool = False) -> dict:
        """FR-OB-03c: generate and queue the 940 now (any delay, a manual-mode
        partner, a resend after a rejection / failure / timeout, or a confirmed
        replacement). Audited through the order's transitions."""
        self.uow.set_reason("Send 940 now (replacement)" if replace else "Send 940 now")
        doc = await outbound.send_now(self.uow, id, replace=replace)
        order = await self.repo.get(id)
        return {"order": self.to_read(order).model_dump(mode="json"), "document_id": doc.id,
                "file_status": doc.file_status, "control": doc.st02, "errors": (doc.body_json or {}).get("errors", [])}

    async def _dc_number(self, o: Order) -> str | None:
        gln = (o.ship_to or {}).get("gln")
        if not gln:
            return None
        return (await self.uow.session.execute(select(DcRouting.dc_number).where(
            DcRouting.partner_code == o.partner_code, DcRouting.ship_to_gln == str(gln).strip(),
            DcRouting.active.is_(True)))).scalar_one_or_none()

    async def _shipments(self, order_id: int) -> list[ShipmentRead]:
        s = self.uow.session
        ships = list((await s.execute(select(Shipment).where(Shipment.order_id == order_id).order_by(Shipment.id))).scalars())
        rows = list((await s.execute(select(ShipmentRow).where(ShipmentRow.shipment_id.in_([x.id for x in ships]))
                                     .order_by(ShipmentRow.shipment_id, ShipmentRow.lx_index, ShipmentRow.w12_index))).scalars()) if ships else []
        return [ShipmentRead.model_validate(x).model_copy(update={
            "rows": [ShipmentRowRead.model_validate(r) for r in rows if r.shipment_id == x.id]}) for x in ships]

    @requires(OPERATE)
    async def retry_shopify(self, shipment_id: int, shipments) -> dict:
        """FR-OB-11: a person's retry of a failed / refused Shopify write; the
        write reads the order's fulfillments first, so nothing is made twice."""
        self.uow.set_reason("Shopify write-back: retry")
        sh = await shipments.retry_shopify(self.uow, shipment_id)
        return {"shipment_id": sh.id, "shopify_status": sh.shopify_status}

    @requires(READ)
    async def eligibility_preview(self, outbound) -> list[dict]:
        """AC-OB-21: the orders the next cycle would send a 940 for (writes nothing)."""
        return await outbound.preview(self.uow.session)

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
        if not (p.abbrev or "").strip():
            raise InvalidInput("partner.no_abbrev", f"partner {p.code} has no abbreviation: set it under "
                               "Configuration → Partners (it starts the Order ref)", {"field": "partner_code"})
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
                      ship_to=data.ship_to.model_dump(exclude_none=True), notes=data.notes, fulfillment_status=F.DRAFT.value,
                      invoice_status=(Inv.NOT_REQUIRED if p.invoice_method == "NONE" else Inv.PENDING).value,
                      first_received_at=datetime.now(timezone.utc), version=1,
                      shadow=await SETTINGS.get(self.s, SHADOW_MODE))
        self.s.add(order)
        await self.s.flush()
        order.source_reference = order_ref(p, str(order.order_number))  # a manual order: <abbrev>-<internal number>
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
        o.requested_delivery_date, o.ship_to, o.notes = data.requested_delivery_date, data.ship_to.model_dump(exclude_none=True), data.notes
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

