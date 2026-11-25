"""Orders as people see them (design §7): the list with the derived order
state, the detail with lines, source documents and the timeline, the review
worklist's "re-check", and manual orders (FR-OB-19a)."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import and_, case, func, or_, select

from app.core.config import settings as app_settings
from app.domain.address import address_flags
from app.domain.carriers import tracking_url
from app.domain.invoicing import CASE_UNIT, amount
from app.domain.lifecycle import backordered, line_state, spread
from app.domain.order_rules import (
    ON_HOLD_FULFILLMENT,
    can_cancel,
    hold_exits,
    order_state,
    payment_satisfied,
    send_940_action,
    to_ea,
)
from app.domain.order_status import (
    HOLD_REASONS as ON_HOLD_REASONS,
)
from app.domain.order_status import (
    NOT_OPEN_STATUSES,
    ORDER_STATUSES,
    DetailFacts,
    StatusFacts,
    delay_start,
    detail_kind,
    display,
    send_time,
    status_detail,
)
from app.domain.order_payment import PAYMENT_STATUSES
from app.platform.uow import Savepoint
from app.platform.clock import clock
from app.services.order_columns import ORDER_COLUMNS
from app.services.record_files import RECORDS, TIMELINES, timeline_of
from app.domain.orders import HOLD_REASONS, ORDER_STATES, SOURCE_TYPES
from app.domain.row_matching import identifiers, line_identifiers
from app.domain.status import FULFILLMENT
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.domain.status_help import next_step
from app.models.config import Connection, DcRouting, Item, ItemUnit, Partner
from app.models.documents import Document, DocumentLink
from app.models.invoices import Invoice, InvoiceLine
from app.models.orders import WITH_FACTS, Order, OrderLine
from app.models.payments import (
    OrderPayment,
    OrderRefund,
    OrderRefundAdjustment,
    OrderRefundLine,
)
from app.models.shipments import Shipment, ShipmentRow
from app.platform.authorization import requires
from app.platform.commands import VersionArg, check_version
from app.platform.errors import BusinessError, InvalidInput, NotFound
from app.platform.query import FilterRule
from app.platform.repository import Repository
from app.platform.resource import RecordFile, Resource
from app.platform.services import ReadOnlyService, Service
from app.platform.views import Preset
from app.schemas.orders import (
    InvoicePricesIn,
    Later945,
    Later945Item,
    ReturnedIn,
    LinePreview,
    LineRead,
    ManualLineIn,
    ManualOrderIn,
    OrderDetail,
    OrderDocumentRead,
    OrderRead,
    PackingRead,
    PaymentRead,
    RefundAdjustmentRead,
    RefundLineRead,
    RefundRead,
    ShipmentRead,
    ShipmentRowRead,
    TimelineEntry,
    TrackingLinkRead,
)
from app.services import oplog, print_docs
from app.services.carriers import carriers_by_scac
from app.services.generated import FileRefused, keep_attempt
from app.services.intake import ConnectionSnapshot
from app.services.order_status_sql import (
    BACKORDERED_SQL,
    ON_HOLD_REASON_SQL,
    ORDER_STATUS_SQL,
    TO_CONFIRM_SQL,
)
from app.services.later_part import waiting_for_press
from app.services.orders import BarcodeLookup, order_ref
from app.services.return_total import refuse_more_than_shipped
from app.services.settings import SETTINGS, SHADOW_MODE, automatic_runs_on

READ = "sales.read"
_DOC_COLUMNS = (Document.id, Document.kind, Document.direction, Document.remote_name, Document.created_at,
                Document.file_status, Document.ack_status, Document.st02, Document.sent_at, Document.error,
                Document.ack_detail)
OPERATE = "sales.order.operate"
INVOICE_MANAGE = "sales.invoice.manage"  # the invoice price is an invoicing decision


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

    def base_query(self):
        """An order as the Orders pages show it: with its facts (models/orders.py order_fact)."""
        return super().base_query().options(WITH_FACTS)
    search_fields = ("source_reference", "source_order_number", "source_order_id", "partner_name_sql", "review_reason", "tags")
    search_numbers = ("order_number",)  # the Internal no., matched exactly
    client_columns = ("status_detail", "shopify", "ship_to_place")  # OrdersPage's own cells
    list_fields = ("order_number", "partner_code", "connection_id", "source_type", "source_order_id",
                   "source_order_number", "source_reference", "order_type", "department", "order_date",
                   "requested_delivery_date", "do_not_deliver_after", "cancel_date", "currency", "subtotal", "tax",
                   "shipping", "discount", "total", "fulfillment_status", "invoice_status", "hold_reason",
                   "review_reason", "historical", "first_received_at", "paid_at", "source_updated_at",
                   "source_cancelled_at", "archived_in_shopify_at", "archive_suppressed", "shopify_financial_status",
                   "shopify_fulfillment_status", "source_name", "tags", "original_total", "total_refunded", "net_amount",
                   "manual_reason", "parent_order_id", "settled_on", "settled_reference", "settled_amount", "settled_note",
                   "settled_at", "settled_by_name", "created_at", "updated_at")
    labels = {"order_number": "Internal no.", "source_reference": "Order ref", "partner_name": "Partner",
              "partner_code": "Partner code", "total_amount": "Total", "total": "Source total", "source_order_number": "Source no.", "source_order_id": "Source ID",
              "order_status": "Status", "on_hold_reason": "On Hold reason", "first_received_at": "Received", "paid_at": "Paid",
              "shopify_financial_status": "Shopify Payment", "shopify_fulfillment_status": "Shopify Fulfillment",
              "archived_in_shopify_at": "Archived", "source_cancelled_at": "Cancelled",
              "source_name": "Sales channel", "connection_name": "Connection", "tags": "Tags", "original_total": "Original total",
              "total_refunded": "Refunded", "net_amount": "Net", "returned_ea": "Returned EA",
              "applied_945s": "Shipments", "over_shipped_ea": "Over shipped EA",
              "delivery_due_date": "Delivery due", "manual_reason": "Reason", "parent_order_id": "Reorder of (no.)",
              "parent_reference": "Reorder of", "received_month": "Month", "ordered_ea": "EA",
              "payment_status": "Payment", "settled_on": "Settled on", "settled_reference": "Settlement ref",
              "settled_amount": "Settled amount", "settled_note": "Settlement note", "settled_at": "Marked settled",
              "settled_by_name": "Settled by"}
    enums = {"source_type": SOURCE_TYPES, "fulfillment_status": tuple(s.value for s in F),
             "invoice_status": tuple(s.value for s in Inv), "hold_reason": HOLD_REASONS,
             "order_state": ORDER_STATES, "order_status": ORDER_STATUSES, "on_hold_reason": ON_HOLD_REASONS,
             "payment_status": PAYMENT_STATUSES}
    # The internal lines stay filterable and sortable, so a saved view on them still lists,
    # but no list offers them any more: they show on the order page, for diagnosis.
    meta_hidden = ("connection_id", "order_status_label", "short_shipped", "status_detail", "status_detail_at", "next_step", "next_step_who", "delay_starts_at", "delay_minutes", "settlement_mode", "shadow", "to_confirm",
                   "fulfillment_status", "invoice_status", "order_state", "hold_reason",
                   # what the 945 left unshipped: no column any more (user 2026-11-12), only the Short shipped mark
                   "backordered_ea")
    sortable = {"order_state": ORDER_STATE_SQL, "order_status": ORDER_STATUS_SQL, "on_hold_reason": ON_HOLD_REASON_SQL, "partner_name": Order.partner_name_sql,
                "connection_name": Order.connection_name_sql, "total_amount": Order.total_amount_sql, "backordered_ea": BACKORDERED_SQL, "returned_ea": Order.returned_ea_sql,
                "applied_945s": Order.applied_945s_sql, "over_shipped_ea": Order.over_shipped_ea_sql,
                "delivery_due_date": Order.delivery_due_sql, "received_month": Order.received_month_sql,
                "ordered_ea": Order.ordered_ea_sql, "parent_reference": Order.parent_reference_sql,
                "payment_status": Order.payment_status_sql}
    filterable = {"order_state": FilterRule(ORDER_STATE_SQL, kind="text"),
                  "order_status": FilterRule(ORDER_STATUS_SQL, kind="text"),
                  "on_hold_reason": FilterRule(ON_HOLD_REASON_SQL, kind="text"),
                  "partner_name": FilterRule(Order.partner_name_sql, kind="text"),
                  "connection_name": FilterRule(Order.connection_name_sql, kind="text"),
                  "total_amount": FilterRule(Order.total_amount_sql, kind="number", cast=lambda v: Decimal(str(v))),
                  "backordered_ea": FilterRule(BACKORDERED_SQL, kind="number", cast=int),
                  "returned_ea": FilterRule(Order.returned_ea_sql, kind="number", cast=int),
                  "applied_945s": FilterRule(Order.applied_945s_sql, kind="number", cast=int),
                  "over_shipped_ea": FilterRule(Order.over_shipped_ea_sql, kind="number", cast=int),
                  "to_confirm": FilterRule(TO_CONFIRM_SQL, kind="bool", cast=lambda v: str(v).lower() == "true"),
                  "received_month": FilterRule(Order.received_month_sql, kind="date"),
                  "ordered_ea": FilterRule(Order.ordered_ea_sql, kind="number", cast=int),
                  "parent_reference": FilterRule(Order.parent_reference_sql, kind="text"),
                  "payment_status": FilterRule(Order.payment_status_sql, kind="text")}
    summable = ("total_amount", "net_amount", "total_refunded", "settled_amount")
    sum_currency = "currency"
    money_fields = ("total_amount", "total", "subtotal", "tax", "shipping", "discount", "original_total", "total_refunded", "net_amount",
                    "settled_amount")
    default_sort = ("-order_number",)
    suggestable = ("partner_name", "source_order_number", "source_reference")
    groupable = ("order_status", "on_hold_reason", "order_state", "partner_name", "connection_name", "partner_code", "source_type", "fulfillment_status", "invoice_status", "hold_reason", "order_date",
                 "currency", "historical", "archive_suppressed", "shopify_financial_status", "source_name", "manual_reason",
                 "received_month", "payment_status")
    # System views (§13.19). Review reason is shown where it says something.
    presets = (
        # The first view is what Orders opens on (unless the person has a default of their own).
        Preset("open", "Open", {"filters": [{"field": "order_status", "op": "not_in", "value": list(NOT_OPEN_STATUSES)}],
                                "hidden": ["review_reason"]}),
        # By channel (user 2026-11-09): each with what that work needs, oldest / most urgent first.
        Preset("open_shopify", "Open Shopify", {
            "filters": [{"field": "order_status", "op": "not_in", "value": list(NOT_OPEN_STATUSES)},
                        {"field": "source_type", "op": "eq", "value": "SHOPIFY"}],
            "sort": [{"field": "first_received_at", "desc": False}],
            "columns": ["source_reference", "first_received_at", "order_status", "status_detail", "shopify", "shopify_financial_status",
                        "ship_to_place", "total_amount"],
            "hidden": ["order_number", "partner_name", "source_order_number", "order_date", "total_refunded", "net_amount",
                       "returned_ea", "review_reason"]}),
        Preset("open_retailers", "Open retailers", {
            "filters": [{"field": "order_status", "op": "not_in", "value": list(NOT_OPEN_STATUSES)},
                        {"field": "source_type", "op": "in", "value": ["EDI_850", "FROM_945"]}],
            "sort": [{"field": "delivery_due_date", "desc": False}],
            "columns": ["source_reference", "partner_name", "source_order_number", "order_date", "delivery_due_date",
                        "cancel_date", "ship_to_place", "order_status", "status_detail", "total_amount", "returned_ea"],
            "hidden": ["order_number", "shopify", "total_refunded", "net_amount", "review_reason"]}),
        Preset("pending", "To fulfill", {"filters": [{"field": "order_status", "op": "eq", "value": "To Fulfill"}], "hidden": ["review_reason"],
                                       "sort": [{"field": "first_received_at", "desc": False}]}),
        # Every problem in one view (user 2026-11-09), grouped by its reason: review, payment, archived,
        # 940 / 997, a second 945, Shopify or invoice failures.
        Preset("on_hold", "On hold", {"filters": [{"field": "order_status", "op": "eq", "value": "On Hold"}],
                                    "group": [{"field": "on_hold_reason", "desc": False}],
                                    "sort": [{"field": "first_received_at", "desc": False}]}),
        # Shipped and not invoiced yet (retailers): waiting for delivery confirmation, or the invoice to go.
        Preset("delivery", "To invoice",
               {"filters": [{"field": "order_status", "op": "in", "value": ["Shipped", "To Invoice"]}],
                "sort": [{"field": "delivery_due_date", "desc": False}], "hidden": ["review_reason"]}),
        Preset("manual_drafts", "Manual drafts", {"filters": [{"field": "source_type", "op": "eq", "value": "MANUAL"},
                                                               {"field": "order_status", "op": "eq", "value": "Draft"}]}),
        # Manual orders by reason (user 2026-11-19: every reason, no separate "free" list): month, then reason, with
        # their EA; Finance narrows it with a filter or a saved view (Sales has no cost; the lines show the items).
        Preset("manual_by_reason", "Manual orders by reason", {
            "filters": [{"field": "source_type", "op": "eq", "value": "MANUAL"},
                        {"field": "order_status", "op": "not_in", "value": ["Draft", "Cancelled"]}],
            "group": [{"field": "received_month", "desc": True}, {"field": "manual_reason", "desc": False}],
            "sort": [{"field": "first_received_at", "desc": True}],
            "columns": ["source_reference", "first_received_at", "manual_reason", "parent_reference", "ordered_ea", "ship_to_place",
                        "order_status"],
            "hidden": ["order_number", "partner_name", "source_order_number", "order_date", "total_amount", "total_refunded",
                       "net_amount", "returned_ea", "review_reason", "shopify", "status_detail"]}),
        # Payment (user 2026-11-23): what Finance has not marked settled yet, Overdue / Due / Not settled, oldest first.
        Preset("to_settle", "To settle", {
            "filters": [{"field": "payment_status", "op": "in", "value": ["Overdue", "Due", "Not settled"]}],
            "group": [{"field": "payment_status", "desc": True}],
            "sort": [{"field": "order_date", "desc": False}],
            "columns": ["source_reference", "partner_name", "order_date", "order_status", "payment_status", "total_amount"],
            "hidden": ["order_number", "source_order_number", "total_refunded", "net_amount", "returned_ea", "review_reason",
                       "shopify", "status_detail"]}),
        Preset("all", "All", {"hidden": ["review_reason"]}),
    )
    historical_column = "historical"


def _delay_start(o: Order) -> datetime | None:
    """When the partner's delay starts; None with no delay (the 940 goes on the next run) or not paid yet."""
    if not o.fact("partner_delay_minutes_sql"):  # 0 (the partner has no delay) or no partner
        return None
    return delay_start(from_receipt=o.fact("partner_settlement_mode_sql") == "INVOICE", received_at=o.first_received_at,
                       paid_at=o.paid_at)


def _send_at(o: Order) -> datetime | None:
    return send_time(from_receipt=o.fact("partner_settlement_mode_sql") == "INVOICE", received_at=o.first_received_at,
                     paid_at=o.paid_at, delay_minutes=o.fact("partner_delay_minutes_sql"))


def status_facts(o: Order) -> StatusFacts:
    """What the rules read of an order, from the row and what was loaded with it."""
    send_at = _send_at(o)
    return StatusFacts(
        delay_over=send_at is not None and send_at <= clock().now(),
        fulfillment=o.fulfillment_status, invoice=o.invoice_status,
        source_type=o.source_type, shadow=bool(o.shadow), archived=o.archived_in_shopify_at is not None,
        fulfilled_in_shopify=o.shopify_fulfillment_status == "fulfilled", cancelled_at_source=o.source_cancelled_at is not None,
        has_945=bool(o.fact("has_945_sql")), shopify_failed=bool(o.fact("shopify_failed_sql")),
        shopify_pending=bool(o.fact("shopify_pending_sql")),
        invoicing=(o.fact("partner_invoice_method_sql") or "NONE") != "NONE", short=(o.backordered_ea or 0) > 0 or bool(o.fact("shadow_short_sql")))


def detail_facts(o: Order) -> DetailFacts:
    return DetailFacts(
        manual_sending=o.fact("partner_sending_mode_sql") == "MANUAL",
        send_at=_send_at(o),
        sent_at=o.fact("sent_940_at_sql"), requested_date=o.requested_delivery_date, review_reason=o.review_reason,
        delivery_due=o.fact("delivery_due_sql"), shipped_on=o.fact("last_ship_date_sql"),
        archived_at=o.archived_in_shopify_at, cancelled_at=o.source_cancelled_at, file_note=o.fact("file_note_sql"),
        shopify_error=o.fact("shopify_error_sql"), invoice_error=o.fact("invoice_error_sql"))


def shadow_figures(lines: list[OrderLine], packed: dict[int, list[PackingRead]]) -> dict[int, dict]:
    """What the shadow run's 945s shipped per line, and what that leaves backordered —
    shown as the shadow run's, never applied. By item, then spread over the item's lines."""
    by_item: dict[object, list[OrderLine]] = {}
    for ln in lines:
        by_item.setdefault(ln.item_code or ln.id, []).append(ln)
    out: dict[int, dict] = {}
    for group in by_item.values():
        rows = [p for ln in group for p in packed.get(ln.id, []) if p.shadow]
        if not rows:
            continue
        open_lines = [ln for ln in group if not ln.removed]
        got = spread(sum(p.qty_shipped_ea for p in rows), [ln.qty_ea or 0 for ln in open_lines])
        for ln, n in zip(open_lines, got):
            out[ln.id] = {"shipped_shadow_ea": n, "backordered_shadow_ea": backordered(ln.qty_ea, n, ln.removed)}
    return out


def _order_actions():
    from app.services.order_actions import ORDER_ACTIONS  # it imports this module
    return ORDER_ACTIONS


class OrderService(ReadOnlyService[Order, OrderRead]):
    repo_cls = OrderRepository
    read_schema = OrderRead
    read_permission = READ
    # Everyone who sees Orders may export them (user 2026-11-24); the full export is the list's fields and whatever else
    # the page or a view of it shows (tests/test_list_resources.py: never less than the page).
    resource = Resource(path="/orders", detail=OrderDetail, timeline=TIMELINES["order"], attachments=RECORDS["order"], actions=lambda: _order_actions(),
                        files=(RecordFile("po.pdf", "po_pdf"), RecordFile("packet.zip", "packet", media_type="application/zip", inline=False)), computed=ORDER_COLUMNS,
                        export_headers="labels", export_fields=(*OrderRepository.list_fields, "partner_name", "payment_status", "ship_to_place", "order_status",
                                       "status_detail", "shopify", "total_amount", "returned_ea", "ordered_ea", "parent_reference",
                                       "delivery_due_date", "received_month"))

    # Whether automatic runs happen: what "by itself" and "the next run" in an order's next step depend on.
    # Read once per request, before the rows; never read (a path that forgot): no step, never a false promise.
    settings: ClassVar[Any] = app_settings
    _runs_on: bool | None = None

    async def _load_runs_on(self) -> None:
        self._runs_on = await automatic_runs_on(self.uow.session, self.settings)

    async def _read_all(self, entities: list[Order]) -> list[OrderRead]:
        await self._load_runs_on()
        return await super()._read_all(entities)

    def to_read(self, o: Order) -> OrderRead:
        out = OrderRead.model_validate(o, from_attributes=True)
        out.order_state = order_state(o.fulfillment_status, o.invoice_status)
        facts = status_facts(o)
        d = display(facts)
        out.order_status, out.on_hold_reason, out.order_status_label = d.order_status, d.on_hold_reason, d.label
        out.short_shipped = d.short_shipped
        details = detail_facts(o)
        out.status_detail, out.status_detail_at = status_detail(d, facts, details)
        out.status_detail_kind = detail_kind(out.status_detail)
        out.delay_starts_at = _delay_start(o)
        out.delay_minutes, out.settlement_mode = o.fact("partner_delay_minutes_sql"), o.fact("partner_settlement_mode_sql")
        step = None if self._runs_on is None else next_step(d, facts, details, clock().today(), runs_on=self._runs_on)
        out.next_step, out.next_step_who = (step.text, step.who) if step else (None, None)
        return out

    async def _detail(self, id: int) -> OrderDetail:
        s = self.uow.session
        o = await self.repo.get(id)
        await self._load_runs_on()
        lines = list((await s.execute(select(OrderLine).where(OrderLine.order_id == id).order_by(OrderLine.line_no))).scalars())
        has_invoice = (await s.execute(select(Invoice.id).where(Invoice.order_id == id))).first() is not None
        docs = (await s.execute(select(*_DOC_COLUMNS)
                                .join(DocumentLink, DocumentLink.document_id == Document.id)
                                .where(DocumentLink.entity_type == "order", DocumentLink.entity_id == id)
                                .order_by(Document.id))).all()
        base = self.to_read(o).model_dump()
        shipments = await self._shipments(id)
        return OrderDetail(
            **base, **{k: getattr(o, k) for k in ("bill_to", "contact", "notes")},  # the amounts, type and department: in the row
            lines=await self._lifecycle(lines), has_invoice=has_invoice,
            documents=[OrderDocumentRead(**d._asdict()) for d in docs],
            shipments=shipments,
            cancellable=can_cancel(o.fulfillment_status),
            send_940=send_940_action(o.fulfillment_status, source_type=o.source_type, historical=o.historical,
                                     has_945=bool(shipments)),
            hold_exits=hold_exits(o.fulfillment_status, o.fulfillment_status_before_hold,
                                  cancelled_at_source=o.source_cancelled_at is not None,
                                  applied_945=any(x.applied for x in shipments)),
            later_945=await self._later_945(o),
            payment_gateways=o.payment_gateways, discount_codes=o.discount_codes, tax_lines=o.tax_lines,
            **await self._payments(id),
            dc_number=await self._dc_number(o), ship_to_flags=address_flags(o.ship_to),
            timeline=sorted(await timeline_of(s, "order", id) + await self._reorders(id),
                            key=lambda e: e.at, reverse=True),
        )

    async def _later_945(self, o: Order) -> Later945 | None:
        """The second 945 of a held order, with what the rule says of adding it."""
        found = None if o.shadow else await waiting_for_press(self.uow.session, o)
        if found is None:
            return None
        sh, _, j = found
        return Later945(shipment_id=sh.id, kind=j.answer.kind, refused=j.answer.refused, items=[
            Later945Item(item_code=i.item_code, carried_ea=i.carried, earlier_ea=i.earlier, this_ea=i.this, above_ea=i.above)
            for i in j.answer.items])

    async def _reorders(self, id: int) -> list[TimelineEntry]:
        """The orders made as a reorder of this one, in its Activity: "Reorder MAN-1043 (Gift)" (user 2026-11-19)."""
        kids = (await self.uow.session.execute(select(Order).where(Order.parent_order_id == id).order_by(Order.id))).scalars()
        return [TimelineEntry(at=k.created_at, user_name=k.created_by_name or "", entity="sales_orders", action="reorder",
                              before=None, after={"order_id": k.id, "order_ref": k.source_reference, "reason": k.manual_reason,
                                                  "status": k.fulfillment_status},
                              reason=f"Reorder {k.source_reference}" + (f" ({k.manual_reason})" if k.manual_reason else ""))
                for k in kids]

    @requires(INVOICE_MANAGE)
    async def set_invoice_prices(self, id: int, body: InvoicePricesIn, version: VersionArg = None) -> OrderDetail:
        """The invoice price per order line before the invoice exists (user
        2026-11-03): the draft copies it instead of the PO price. Equal to the PO
        price (or null) means "the PO price". A price that differs from the PO
        needs a reason (as on the invoice), kept in the audit and the log. Once the
        draft exists the invoice is the one place to change it."""
        s = self.uow.session
        o = await self.repo.get(id, for_update=True)
        check_version(o, version, "this order")
        if (await s.execute(select(Invoice.id).where(Invoice.order_id == id))).first() is not None:
            raise BusinessError("order.invoice_exists", "the invoice is drafted: change the price on the invoice")
        if o.fulfillment_status == F.CANCELLED:
            raise BusinessError("order.cancelled", "the order is cancelled")
        mine = {ln.id: ln for ln in (await s.execute(select(OrderLine).where(
            OrderLine.order_id == id, OrderLine.removed.is_(False)).with_for_update())).scalars()}
        changed: list[tuple[OrderLine, Decimal | None]] = []
        for c in body.lines:
            ln = mine.get(c.id)
            if ln is None:
                raise BusinessError("order.line", f"line {c.id} is not an open line of this order", {"line_id": c.id})
            new = None if c.invoice_unit_price is None or c.invoice_unit_price == ln.unit_price else c.invoice_unit_price
            if new != ln.invoice_unit_price:
                changed.append((ln, new))
        reason = (body.reason or "").strip()
        if any(new is not None for _, new in changed) and len(reason) < 3:
            raise BusinessError("invoice.reason_required", "say why the invoice price differs from the PO (a few words)",
                                {"field": "reason"}, status_code=422)
        if changed:
            self.uow.set_reason(f"invoice price: {reason}" if reason else "invoice price back to the PO price")
            await oplog.write(s, "INFO", "invoice", f"order {o.label}: invoice price " + "; ".join(
                f"line {ln.line_no} {ln.item_code or ''} {ln.invoice_unit_price if ln.invoice_unit_price is not None else ln.unit_price}"
                f" → {new if new is not None else ln.unit_price}{'' if new is not None else ' (PO)'}" for ln, new in changed)
                + (f" — {reason}" if reason else ""), order_id=o.id, context={"reason": reason or None})
            for ln, new in changed:
                ln.invoice_unit_price = new
            await s.flush()
        return await self.detail(id)

    @requires(INVOICE_MANAGE)
    async def set_returned(self, id: int, body: ReturnedIn, version: VersionArg = None) -> OrderDetail:
        """What the customer returned (or refused) per line, before the invoice goes (user 2026-11-12):
        delivered = shipped − returned, and the invoice bills the delivered. With the delivery already
        confirmed the delivered follows, and a draft invoice's line too (whole cases only; its price
        stays). Once the invoice is queued or sent the number is locked: a later return is recorded with
        Record a return. Every change has its reason, in the audit and the log."""
        s = self.uow.session
        o = await self.repo.get(id, for_update=True)
        check_version(o, version, "this order")
        if o.fulfillment_status == F.CANCELLED:
            raise BusinessError("order.cancelled", "the order is cancelled")
        if o.invoice_status in (Inv.QUEUED.value, Inv.SENT.value):
            raise BusinessError("returned.invoice_sent", "the invoice is sent: record a later return with Record a return",
                                {"status": o.invoice_status})
        mine = {ln.id: ln for ln in (await s.execute(select(OrderLine).where(
            OrderLine.order_id == id, OrderLine.removed.is_(False)).with_for_update())).scalars()}
        inv = (await s.execute(select(Invoice).where(Invoice.order_id == id).with_for_update())).scalar_one_or_none()
        ilines = {il.order_line_id: il for il in (await s.execute(select(InvoiceLine).where(
            InvoiceLine.invoice_id == inv.id))).scalars()} if inv else {}
        changed: list[tuple[OrderLine, int, int]] = []
        for c in body.lines:
            ln = mine.get(c.id)
            if ln is None:
                raise BusinessError("order.line", f"line {c.id} is not an open line of this order", {"line_id": c.id})
            if ln.shipped_ea is None:
                raise BusinessError("returned.not_shipped", f"line {ln.line_no} has not shipped: nothing can come back",
                                    {"line_id": ln.id})
            if c.returned_ea > ln.shipped_ea:
                raise BusinessError("returned.more_than_shipped", f"line {ln.line_no}: returned {c.returned_ea} EA is more than "
                                    f"the {ln.shipped_ea} EA shipped", {"line_id": ln.id, "field": "returned_ea"}, status_code=422)
            if c.returned_ea != ln.returned_ea:
                changed.append((ln, ln.returned_ea, c.returned_ea))
        if not changed:
            return await self.detail(id)
        await refuse_more_than_shipped(s, id, returned={ln.id: new for ln, _old, new in changed})  # with what is recorded after shipment
        reason = body.reason.strip()
        cases = dict((await s.execute(select(ItemUnit.item_code, ItemUnit.factor_ea).where(
            ItemUnit.item_code.in_({ln.item_code for ln, _, _ in changed if ln.item_code}), ItemUnit.unit == CASE_UNIT,
            ItemUnit.active.is_(True)))).all())
        for ln, _old, new in changed:
            ln.returned_ea = new
            if ln.delivered_qty is not None:  # the delivery is confirmed: what it says follows
                ln.delivered_qty = ln.shipped_ea - new
            il = ilines.get(ln.id)
            if inv is None:
                continue
            delivered = ln.shipped_ea - new
            if il is None:
                if delivered:
                    raise BusinessError("returned.invoice_line", f"line {ln.line_no} is not on the invoice: change it on the invoice",
                                        {"line_id": ln.id})
                continue
            factor = int(cases.get(ln.item_code) or 0)
            whole, rest = divmod(delivered, factor) if factor else (0, delivered)
            if rest:
                raise BusinessError("returned.part_case", f"line {ln.line_no}: {delivered} EA delivered is not a whole number of "
                                    f"cases{f' of {factor}' if factor else ''}: the invoice bills cases", {"line_id": ln.id, "field": "returned_ea"},
                                    status_code=422)
            il.qty, il.amount = whole, amount(whole, il.unit_price)
        if inv is not None:
            inv.subtotal = inv.total = sum((il.amount for il in ilines.values()), Decimal(0))
        self.uow.set_reason(f"returned: {reason}")
        await oplog.write(s, "INFO", "invoice", f"order {o.label}: returned " + "; ".join(
            f"line {ln.line_no} {ln.item_code or ''} {old} → {new} EA (delivered {ln.shipped_ea - new})" for ln, old, new in changed)
            + f" — {reason}" + (f"; invoice {inv.invoice_number} follows" if inv is not None else ""),
            order_id=o.id, context={"reason": reason})
        await s.flush()
        await s.refresh(o, ["returned_ea_sql"])  # the order's total, read in this unit before the change
        return await self.detail(id)

    async def _run_send_940(self, id: int, outbound, *, replace: bool, version: VersionArg, reason: str | None):
        self.uow.set_reason(f"940 replaced: {(reason or '').strip()}" if replace else "Send 940 now")
        try:
            return await outbound.send_now(self.uow, id, replace=replace, version=version, reason=reason)
        except FileRefused as e:
            await self._attempt(outbound, id, "replace_refused", reason, e)
            raise

    async def _attempt(self, outbound, id: int, act: str, why: str | None, e: FileRefused, context: dict | None = None) -> None:
        o = await self.uow.session.get(Order, id, populate_existing=True)
        await keep_attempt(outbound.uow_factory, self.uow, (Order, id), act, why, e, step="order", label=f"order {o.label}", context=context, order_id=id)

    @requires(OPERATE)
    async def amend(self, id: int, body, outbound, version: VersionArg = None) -> dict:
        """User 2026-11-13: an order sent wrong, corrected and sent again (app/services/order_amend)."""
        from app.services.order_amend import amend_order
        lines = [x.model_dump() for x in body.lines]
        for x in lines:
            if not x["removed"] and (not x["item_code"] or x["ordered_qty"] is None):
                raise BusinessError("order.amend_line", f"line {x['id']}: an item and a quantity, or take it off")
        try:
            async with Savepoint.of(self.uow):  # its 940 refused: not a line of it stays, whoever catches it
                out = await amend_order(self.uow, outbound, id, lines, body.reason, carrier=body.carrier, version=version)
        except FileRefused as e:
            await self._attempt(outbound, id, "amend_refused", body.reason, e, {"asked": body.model_dump(mode="json")})
            raise
        order = await self.repo.get(id)
        await self._load_runs_on()  # the next step needs it (else none is shown)
        return {"order": self.to_read(order).model_dump(mode="json"), **out}

    async def _dc_number(self, o: Order) -> str | None:
        gln = (o.ship_to or {}).get("gln")
        if not gln:
            return None
        return (await self.uow.session.execute(select(DcRouting.dc_number).where(
            DcRouting.partner_code == o.partner_code, DcRouting.ship_to_gln == str(gln).strip(),
            DcRouting.active.is_(True)))).scalar_one_or_none()

    async def _lifecycle(self, lines: list[OrderLine]) -> list[LineRead]:
        """Each line with what became of it: invoiced (the invoice's lines for
        it), its state, and the 945 rows packed for it (every 945 kept — a
        shadow-run or second one is marked, not hidden: the lots went out)."""
        s = self.uow.session
        ids = [ln.id for ln in lines]
        inv: dict[int, list[InvoiceLine]] = {}
        packed: dict[int, list[PackingRead]] = {}
        if ids:
            for il in (await s.execute(select(InvoiceLine).where(InvoiceLine.order_line_id.in_(ids))
                                       .order_by(InvoiceLine.line_no))).scalars():
                inv.setdefault(il.order_line_id, []).append(il)
            for r, sh in (await s.execute(select(ShipmentRow, Shipment).join(Shipment, Shipment.id == ShipmentRow.shipment_id)
                                          .where(ShipmentRow.order_line_id.in_(ids))
                                          .order_by(Shipment.id, ShipmentRow.lx_index, ShipmentRow.w12_index))).all():
                packed.setdefault(r.order_line_id, []).append(PackingRead(
                    shipment_id=sh.id, document_id=sh.document_id, sscc=r.sscc, lot=r.lot, expiry=r.expiry,
                    qty_shipped_ea=r.qty_shipped_ea, tracking=r.tracking or ", ".join(sh.tracking or []) or None,
                    ship_date=sh.ship_date, applied=sh.applied, shadow=sh.shadow))
        out = []
        shadow = shadow_figures(lines, packed)
        for ln in lines:
            ils = inv.get(ln.id, [])
            out.append(LineRead.model_validate(ln).model_copy(update={
                "invoiced_qty": sum(il.qty for il in ils) if ils else None,
                # Invoiced: the invoice's price per its unit (CA). Before: the price the
                # draft will take, per order unit — the line's own, else the PO's.
                "invoice_unit": ils[0].uom if ils else None,
                "invoice_price": ils[0].unit_price if ils else (
                    None if ln.removed else ln.invoice_unit_price if ln.invoice_unit_price is not None else ln.unit_price),
                "backordered_ea": backordered(ln.qty_ea, ln.shipped_ea, ln.removed),
                **shadow.get(ln.id, {}),
                "invoice_amount": sum((il.amount for il in ils), Decimal(0)) if ils else None,
                "state": line_state(removed=ln.removed, qty_ea=ln.qty_ea,
                                    shipped_ea=ln.shipped_ea if ln.shipped_ea is not None else shadow.get(ln.id, {}).get("shipped_shadow_ea"),
                                    delivered_ea=ln.delivered_qty, invoiced=bool(ils)),
                "packing": packed.get(ln.id, []),
            }))
        return out

    async def _payments(self, order_id: int) -> dict:
        """The order's Shopify transactions and refunds (lines, adjustments), oldest first."""
        s = self.uow.session
        pays = (await s.execute(select(OrderPayment).where(OrderPayment.order_id == order_id)
                                .order_by(OrderPayment.processed_at.asc().nulls_last(), OrderPayment.shopify_id))).scalars()
        refunds = list((await s.execute(select(OrderRefund).where(OrderRefund.order_id == order_id)
                                        .order_by(OrderRefund.refunded_at.asc().nulls_last(), OrderRefund.shopify_id))).scalars())
        ids = [r.id for r in refunds]
        lines = list((await s.execute(select(OrderRefundLine).where(OrderRefundLine.refund_id.in_(ids))
                                      .order_by(OrderRefundLine.id))).scalars()) if ids else []
        adjs = list((await s.execute(select(OrderRefundAdjustment).where(OrderRefundAdjustment.refund_id.in_(ids))
                                     .order_by(OrderRefundAdjustment.id))).scalars()) if ids else []
        return {"payments": [PaymentRead.model_validate(p) for p in pays],
                "refunds": [RefundRead.model_validate(r).model_copy(update={
                    "lines": [RefundLineRead.model_validate(x) for x in lines if x.refund_id == r.id],
                    "adjustments": [RefundAdjustmentRead.model_validate(x) for x in adjs if x.refund_id == r.id]})
                    for r in refunds]}

    async def _shipments(self, order_id: int) -> list[ShipmentRead]:
        """The order's 945s with what a person needs to read them: the ship-to, the
        carrier by name and its tracking links, the control numbers, when each came,
        its part of a split shipment, and each row's line, item and cases."""
        s = self.uow.session
        ships = list((await s.execute(select(Shipment).where(Shipment.order_id == order_id).order_by(Shipment.id))).scalars())
        if not ships:
            return []
        rows = list((await s.execute(select(ShipmentRow).where(ShipmentRow.shipment_id.in_([x.id for x in ships]))
                                     .order_by(ShipmentRow.shipment_id, ShipmentRow.lx_index, ShipmentRow.w12_index))).scalars())
        docs = {d.id: d for d in (await s.execute(select(Document.id, Document.remote_name, Document.created_at, Document.isa13,
                                                         Document.gs06).where(Document.id.in_({x.document_id for x in ships})))).all()}
        carriers = await carriers_by_scac(s, [x.carrier for x in ships])
        codes = {r.item_code for r in rows if r.item_code}
        items = {i.item_code: i for i in (await s.execute(select(Item).where(Item.item_code.in_(codes)))).scalars()} if codes else {}
        cases = dict((await s.execute(select(ItemUnit.item_code, ItemUnit.factor_ea).where(
            ItemUnit.item_code.in_(codes), ItemUnit.unit == "CASE", ItemUnit.active.is_(True)))).all()) if codes else {}
        lines = list((await s.execute(select(OrderLine).where(OrderLine.order_id == order_id, OrderLine.removed.is_(False)))).scalars())
        line_no = dict((await s.execute(select(OrderLine.id, OrderLine.line_no).where(OrderLine.order_id == order_id))).all())
        master = {i.item_code: i for i in (await s.execute(select(Item).where(
            Item.item_code.in_([ln.item_code for ln in lines if ln.item_code])))).scalars()}

        def same_item(r: ShipmentRow) -> list[int]:
            """The lines Resolve may link this row to: its own item only."""
            ident = identifiers(r.item_code, r.upc)
            return [ln.id for ln in lines if ident & line_identifiers(
                ln.item_code, ln.source, *((master[ln.item_code].upc, master[ln.item_code].gtin) if ln.item_code in master else (None, None)))]
        parts = [(x.order_key_received or "")[-1:].lower() for x in ships]
        split = len(ships) > 1 and all(p.isalpha() for p in parts)
        out = []
        for x, part in zip(ships, parts):
            carrier = carriers.get((x.carrier or "").strip().upper())
            template = carrier.tracking_url if carrier else None
            d = docs.get(x.document_id)
            out.append(ShipmentRead.model_validate(x).model_copy(update={
                "carrier_name": carrier.name if carrier else None,
                "tracking_links": [TrackingLinkRead(number=t, url=tracking_url(template, t)) for t in (x.tracking or [])],
                "isa13": d.isa13 if d else None, "gs06": d.gs06 if d else None,
                "document_name": d.remote_name if d else None, "received_at": d.created_at if d else None,
                "part": part if (split or (part.isalpha() and (x.order_key_received or "")[:-1].isdigit())) else None,
                "rows": [ShipmentRowRead.model_validate(r).model_copy(update={
                    # A shadow 945's row is flagged as an applied one would be; Resolve is refused on it.
                    "needs_review": r.needs_review or (x.shadow and r.order_line_id is None),
                    "line_no": line_no.get(r.order_line_id) if r.order_line_id else None,
                    "description": items[r.item_code].description if r.item_code in items else None,
                    "case_factor": int(cases[r.item_code]) if r.item_code in cases else None,
                    "tracking_url": tracking_url(template, r.tracking),
                    "matching_lines": same_item(r) if r.needs_review or r.order_line_id is None else [],
                }) for r in rows if r.shipment_id == x.id],
            }))
        return out

    @requires(READ)
    async def po_pdf(self, order_id: int) -> tuple[str, bytes]:
        o = await self.repo.get(order_id)
        return f"PO-{o.source_order_number or o.order_number}.pdf", await print_docs.po_pdf(self.uow.session, o)

    @requires(READ)
    async def packet(self, order_id: int) -> tuple[str, bytes]:
        """The order's audit packet: every stored file and the PDFs (FR-OB-55)."""
        o = await self.repo.get(order_id)
        return f"Order-{o.ref or o.order_number}.zip", await print_docs.packet(self.uow.session, o)

    @requires(READ)  # it carries the customer's name and address (found by the coverage test, 2026-11-10)
    async def packing_list(self, order_id: int, shipment_id: int) -> tuple[str, bytes]:
        """One shipment's packing list (PDF): pallets → items → lots → expiry →
        qty, with tracking, BOL and its 945. Any 945 kept — a shadow-run or
        second one says so on the page."""
        s = self.uow.session
        o = await self.repo.get(order_id)
        sh = (await s.execute(select(Shipment).where(Shipment.id == shipment_id, Shipment.order_id == order_id))).scalar_one_or_none()
        if sh is None:
            raise NotFound("Shipment", shipment_id)
        pdf = await print_docs.shipment_pdf(s, o, sh)
        return f"PackingList-{o.ref}-{sh.id}.pdf", pdf

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

    async def _read_barcodes(self, order_id: int, barcodes: BarcodeLookup | None) -> None:
        if barcodes is None:
            return
        rows = (await self.uow.session.execute(select(OrderLine.source).where(
            OrderLine.order_id == order_id, OrderLine.item_code.is_(None)))).scalars()
        for variant in {str(r.get("variant_id") or "").strip() for r in rows} - {""}:
            await barcodes.get(variant)

    async def _barcodes(self, o: Order, shopify) -> BarcodeLookup | None:
        """The store the order came from: the active SHOPIFY connection of its
        partner (one per partner in practice; the first by id otherwise)."""
        if o.source_type != "SHOPIFY" or shopify is None:
            return None
        conn = (await self.uow.session.execute(select(Connection).where(
            Connection.kind == "SHOPIFY", Connection.partner_code == o.partner_code, Connection.active.is_(True))
            .order_by(Connection.id).limit(1))).scalar_one_or_none()
        return BarcodeLookup(shopify, ConnectionSnapshot.of(conn)) if conn else None


def manual_address(a: dict | None) -> dict:
    """Any order's address in a manual order's shape (AddressIn): Shopify's country_code / province_code are the codes
    (its country / province may be full names), its company the attention line; any other key dropped (review
    2026-11-19: a Reorder from a Shopify order could not be saved: AddressIn forbids extra keys)."""
    a = dict(a or {})
    out = {k: a[k] for k in ("name", "attention", "phone", "address1", "address2", "city", "zip") if a.get(k) not in (None, "")}
    country = a.get("country_code") or a.get("country")
    if country and len(str(country).strip()) == 2:
        out["country"] = str(country).strip().upper()
    province = a.get("province_code") or a.get("province")
    if province:
        out["province"] = str(province).strip()
    if not out.get("attention") and a.get("company"):
        out["attention"] = a["company"]
    return out


class ManualOrderService(Service):
    """FR-OB-19a. Draft is never eligible; Submit makes it Pending and it
    follows the same flows as any other order. Every step is audited."""

    def __init__(self, uow) -> None:
        super().__init__(uow)

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

    async def _express_allowed(self, carrier: str | None) -> None:
        from app.services.settings import MANUAL_EXPRESS_OFFERED
        if carrier == "Purolator Express" and not await SETTINGS.get(self.s, MANUAL_EXPRESS_OFFERED):
            raise InvalidInput("order.carrier_express", "Purolator Express is not offered (Orders → Manual order settings)", {"field": "carrier"})

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
            w = item.gross_weight_lb_per_ea
            out.append(LinePreview(item_code=ln.item_code, quantity=ln.quantity, unit=ln.unit, qty_ea=qty_ea, problem=why,
                                   weight_lb=(Decimal(qty_ea) * Decimal(w)).quantize(Decimal("0.01")) if qty_ea and w is not None else None))
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
                      shipping_method=data.carrier, manual_reason=data.reason,
                      invoice_status=(Inv.NOT_REQUIRED if p.invoice_method == "NONE" else Inv.PENDING).value,
                      first_received_at=clock().now(), version=1,
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
            raise NotFound("Order", id, f"manual order {id} not found")
        if o.fulfillment_status != F.DRAFT:
            raise BusinessError("order.not_draft", "only a Draft can be changed or submitted",
                                {"status": o.fulfillment_status})
        check_version(o, version, "this order")
        return o

    @requires(OPERATE)
    async def update(self, id: int, data: ManualOrderIn, version: int | None) -> Order:
        o = await self._draft(id, version)
        p = await self._partner(data.partner_code)
        self.uow.set_reason("manual order: edit draft")
        o.partner_code, o.source_order_number = p.code, data.customer_reference
        o.requested_delivery_date, o.ship_to, o.notes = data.requested_delivery_date, data.ship_to.model_dump(exclude_none=True), data.notes
        o.shipping_method, o.manual_reason = data.carrier, data.reason
        for ln in (await self.s.execute(select(OrderLine).where(OrderLine.order_id == o.id))).scalars():
            await self.s.delete(ln)
        await self.s.flush()
        for ln in self._lines(o, await self._preview(data.lines)):
            self.s.add(ln)
        await self.s.flush()
        return o

    async def _run_submit(self, o: Order) -> Order:
        """The work of a submit, on a draft already locked and checked."""
        bad = list((await self.s.execute(select(OrderLine.line_no, OrderLine.problem).where(
            OrderLine.order_id == o.id, OrderLine.problem.is_not(None)))).all())
        if bad:
            raise InvalidInput("order.lines_invalid", "fix every line before submitting",
                               {"field": "lines", "lines": [{"line_no": n, "problem": p} for n, p in bad]})
        if (o.shipping_method or "") not in ("Purolator", "Purolator Express", "LTL"):
            raise InvalidInput("order.carrier", "choose how it ships: Purolator, Purolator Express or LTL", {"field": "carrier"})
        from app.services.settings import MANUAL_REASONS
        reasons = await SETTINGS.get(self.s, MANUAL_REASONS)
        if o.manual_reason not in reasons:
            raise InvalidInput("order.reason", "choose why this order is made: " + ", ".join(reasons), {"field": "reason"})
        if (await self.s.execute(select(func.count()).select_from(OrderLine).where(
                OrderLine.order_id == o.id, OrderLine.removed.is_(False)))).scalar_one() == 0:
            raise InvalidInput("order.no_lines", "add at least one line before submitting", {"field": "lines"})
        await self._express_allowed(o.shipping_method)
        p = await self._partner(o.partner_code)
        self.uow.set_reason("manual order: submit")
        now = clock().now()
        o.first_received_at = now  # the delay runs from submission, not from when the draft was started
        if payment_satisfied(p.settlement_mode, "MANUAL", None) and p.settlement_mode == "PREPAID":
            o.paid_at = now
        FULFILLMENT.transition(o, F.PENDING, actor=self.uow.actor, events=self.uow.events, reason="manual order submitted")
        await self.s.flush()
        return o

    @requires(OPERATE)
    async def reorder(self, source_id: int) -> Order:
        """Reorder for this customer (user 2026-11-19): a Draft manual order copied from any order — its ship-to,
        contact and carrier, its Order ref as the customer's reference, a note, a link back — with no lines; a person
        adds the lines and a reason, then submits. The 945 of what it sends then matches it: no conflict on the source."""
        src = (await self.s.execute(select(Order).where(Order.id == source_id))).scalar_one_or_none()
        if src is None:
            raise NotFound("Order", source_id)
        p = await self._reorder_partner(src)
        self.uow.set_reason(f"reorder of {src.source_reference}")
        ref = src.source_reference or str(src.order_number)
        carrier = src.shipping_method if src.shipping_method in ("Purolator", "Purolator Express", "LTL") else None
        ship = manual_address(src.ship_to)
        if not ship.get("phone"):  # a manual order's 940 needs one: the customer's, where the source has it
            phone = (src.contact or {}).get("phone") or (src.bill_to or {}).get("phone")
            if phone:
                ship["phone"] = phone
        order = Order(partner_code=p.code, source_type="MANUAL", source_order_id=f"MANUAL-{uuid.uuid4().hex}",
                      source_order_number=ref[:50], ship_to=ship or None, contact=src.contact, notes=f"Reorder of {ref}",
                      fulfillment_status=F.DRAFT.value, shipping_method=carrier, parent_order_id=src.id,
                      invoice_status=(Inv.NOT_REQUIRED if p.invoice_method == "NONE" else Inv.PENDING).value,
                      first_received_at=clock().now(), version=1, shadow=await SETTINGS.get(self.s, SHADOW_MODE))
        self.s.add(order)
        await self.s.flush()
        order.source_reference = order_ref(p, str(order.order_number))
        await self.s.flush()
        return order

    async def _reorder_partner(self, src: Order) -> Partner:
        """The source's own partner when it is a manual one; else the one active MANUAL partner (dev2: "Manual",
        no invoice)."""
        own = (await self.s.execute(select(Partner).where(Partner.code == src.partner_code))).scalar_one_or_none()
        if own is not None and own.channel == "MANUAL" and own.active:
            return await self._partner(own.code)
        manual = list((await self.s.execute(select(Partner).where(Partner.channel == "MANUAL", Partner.active.is_(True))
                                            .order_by(Partner.code))).scalars())
        if len(manual) != 1:
            raise InvalidInput("order.reorder_partner", "a reorder needs exactly one active MANUAL partner (Configuration → "
                               f"Partners); there are {len(manual)}", {"field": "partner_code"})
        return await self._partner(manual[0].code)

    async def _run_cancel(self, o: Order) -> Order:
        self.uow.set_reason("manual order: cancel draft")
        FULFILLMENT.transition(o, F.CANCELLED, actor=self.uow.actor, events=self.uow.events, reason="draft cancelled")
        await self.s.flush()
        return o

