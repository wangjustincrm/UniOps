"""Orders as people see them (design §7): the list with the derived order
state, the detail with lines, source documents and the timeline, the review
worklist's "re-check", and manual orders (FR-OB-19a)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, case, or_, select

from app.domain.address import address_flags
from app.domain.carriers import tracking_url
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
    detail_kind,
    display,
    send_time,
    status_detail,
)
from app.domain.orders import HOLD_REASONS, ORDER_STATES, SOURCE_TYPES
from app.domain.row_matching import identifiers, line_identifiers
from app.domain.status import FULFILLMENT
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.models.config import Connection, DcRouting, Item, ItemUnit, Partner
from app.models.documents import Document, DocumentLink
from app.models.invoices import Invoice, InvoiceLine
from app.models.orders import Order, OrderLine
from app.models.payments import (
    OrderPayment,
    OrderRefund,
    OrderRefundAdjustment,
    OrderRefundLine,
)
from app.models.records import AuditRecord
from app.models.shipments import Shipment, ShipmentRow
from app.platform.authorization import requires
from app.platform.commands import check_version
from app.platform.errors import BusinessError, InvalidInput, NotFound
from app.platform.query import FilterRule
from app.platform.repository import Repository
from app.platform.services import ReadOnlyService
from app.platform.views import Preset
from app.schemas.orders import (
    InvoicePricesIn,
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
    ReviewResult,
    ShipmentRead,
    ShipmentRowRead,
    TimelineEntry,
    TrackingLinkRead,
)
from app.services import oplog, print_docs
from app.services.carriers import carriers_by_scac
from app.services.intake import ConnectionSnapshot
from app.services.invoice_pdf import PackingList, PackRow, render_packing
from app.services.invoice_pdf import Party as PdfParty
from app.services.invoicing import SELLER
from app.services.order_status_sql import (
    BACKORDERED_SQL,
    ON_HOLD_REASON_SQL,
    ORDER_STATUS_SQL,
    TO_CONFIRM_SQL,
)
from app.services.orders import BarcodeLookup, OrderSync, order_ref
from app.services.settings import SETTINGS, SHADOW_MODE

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
    search_fields = ("source_reference", "source_order_number", "source_order_id", "partner_name_sql", "review_reason", "tags")
    search_numbers = ("order_number",)  # the Internal no., matched exactly
    list_fields = ("order_number", "partner_code", "connection_id", "source_type", "source_order_id",
                   "source_order_number", "source_reference", "order_type", "department", "order_date",
                   "requested_delivery_date", "do_not_deliver_after", "cancel_date", "currency", "subtotal", "tax",
                   "shipping", "discount", "total", "fulfillment_status", "invoice_status", "hold_reason",
                   "review_reason", "historical", "first_received_at", "paid_at", "source_updated_at",
                   "source_cancelled_at", "archived_in_shopify_at", "archive_suppressed", "shopify_financial_status",
                   "shopify_fulfillment_status", "source_name", "tags", "original_total", "total_refunded", "net_amount",
                   "created_at", "updated_at")
    labels = {"order_number": "Internal no.", "source_reference": "Order ref", "partner_name": "Partner",
              "partner_code": "Partner code", "total_amount": "Total", "total": "Source total", "source_order_number": "Source no.", "source_order_id": "Source ID",
              "order_status": "Status", "on_hold_reason": "On Hold reason", "first_received_at": "Received", "paid_at": "Paid",
              "shopify_financial_status": "Shopify Payment", "shopify_fulfillment_status": "Shopify Fulfillment",
              "archived_in_shopify_at": "Archived", "source_cancelled_at": "Cancelled",
              "source_name": "Sales channel", "tags": "Tags", "original_total": "Original total",
              "total_refunded": "Refunded", "net_amount": "Net", "backordered_ea": "Backordered EA",
              "delivery_due_date": "Delivery due"}
    enums = {"source_type": SOURCE_TYPES, "fulfillment_status": tuple(s.value for s in F),
             "invoice_status": tuple(s.value for s in Inv), "hold_reason": HOLD_REASONS,
             "order_state": ORDER_STATES, "order_status": ORDER_STATUSES, "on_hold_reason": ON_HOLD_REASONS}
    # The internal lines stay filterable and sortable, so a saved view on them still lists,
    # but no list offers them any more: they show on the order page, for diagnosis.
    meta_hidden = ("order_status_label", "short_shipped", "status_detail", "status_detail_at", "shadow", "to_confirm",
                   "fulfillment_status", "invoice_status", "order_state", "hold_reason")
    sortable = {"order_state": ORDER_STATE_SQL, "order_status": ORDER_STATUS_SQL, "on_hold_reason": ON_HOLD_REASON_SQL, "partner_name": Order.partner_name_sql,
                "total_amount": Order.total_amount_sql, "backordered_ea": BACKORDERED_SQL, "delivery_due_date": Order.delivery_due_sql}
    filterable = {"order_state": FilterRule(ORDER_STATE_SQL, kind="text"),
                  "order_status": FilterRule(ORDER_STATUS_SQL, kind="text"),
                  "on_hold_reason": FilterRule(ON_HOLD_REASON_SQL, kind="text"),
                  "partner_name": FilterRule(Order.partner_name_sql, kind="text"),
                  "total_amount": FilterRule(Order.total_amount_sql, kind="number", cast=lambda v: Decimal(str(v))),
                  "backordered_ea": FilterRule(BACKORDERED_SQL, kind="number", cast=int),
                  "to_confirm": FilterRule(TO_CONFIRM_SQL, kind="bool", cast=lambda v: str(v).lower() == "true")}
    summable = ("total_amount", "net_amount", "total_refunded")
    sum_currency = "currency"
    default_sort = ("-order_number",)
    suggestable = ("partner_name", "source_order_number", "source_reference")
    groupable = ("order_status", "on_hold_reason", "order_state", "partner_name", "partner_code", "source_type", "fulfillment_status", "invoice_status", "hold_reason", "order_date",
                 "currency", "historical", "archive_suppressed", "shopify_financial_status", "source_name")
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
                       "backordered_ea", "review_reason"]}),
        Preset("open_retailers", "Open retailers", {
            "filters": [{"field": "order_status", "op": "not_in", "value": list(NOT_OPEN_STATUSES)},
                        {"field": "source_type", "op": "in", "value": ["EDI_850", "FROM_945"]}],
            "sort": [{"field": "delivery_due_date", "desc": False}],
            "columns": ["source_reference", "partner_name", "source_order_number", "order_date", "delivery_due_date",
                        "cancel_date", "ship_to_place", "order_status", "status_detail", "total_amount", "backordered_ea"],
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
        Preset("all", "All", {"hidden": ["review_reason"]}),
    )
    historical_column = "historical"


def _send_at(o: Order) -> datetime | None:
    return send_time(from_receipt=o.fact("partner_settlement_mode_sql") == "INVOICE", received_at=o.first_received_at,
                     paid_at=o.paid_at, delay_minutes=o.fact("partner_delay_minutes_sql"))


def status_facts(o: Order) -> StatusFacts:
    """What the rules read of an order, from the row and what was loaded with it."""
    send_at = _send_at(o)
    return StatusFacts(
        delay_over=send_at is not None and send_at <= datetime.now(timezone.utc),
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


class OrderService(ReadOnlyService[Order, OrderRead]):
    repo_cls = OrderRepository
    read_schema = OrderRead
    read_permission = READ

    def to_read(self, o: Order) -> OrderRead:
        out = OrderRead.model_validate(o, from_attributes=True)
        out.order_state = order_state(o.fulfillment_status, o.invoice_status)
        facts = status_facts(o)
        d = display(facts)
        out.order_status, out.on_hold_reason, out.order_status_label = d.order_status, d.on_hold_reason, d.label
        out.short_shipped = d.short_shipped
        out.status_detail, out.status_detail_at = status_detail(d, facts, detail_facts(o))
        out.status_detail_kind = detail_kind(out.status_detail)
        return out

    @requires(READ)
    async def detail(self, id: int) -> OrderDetail:
        s = self.uow.session
        o = await self.repo.get(id)
        lines = list((await s.execute(select(OrderLine).where(OrderLine.order_id == id).order_by(OrderLine.line_no))).scalars())
        has_invoice = (await s.execute(select(Invoice.id).where(Invoice.order_id == id))).first() is not None
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
        shipments = await self._shipments(id)
        return OrderDetail(
            **base, **{k: getattr(o, k) for k in ("bill_to", "contact", "notes", "subtotal", "tax", "shipping",
                                                   "discount", "department", "order_type")},
            lines=await self._lifecycle(lines), has_invoice=has_invoice,
            documents=[OrderDocumentRead(**d._asdict()) for d in docs],
            shipments=shipments,
            cancellable=can_cancel(o.fulfillment_status),
            send_940=send_940_action(o.fulfillment_status, source_type=o.source_type, historical=o.historical,
                                     has_945=bool(shipments)),
            hold_exits=hold_exits(o.fulfillment_status, o.fulfillment_status_before_hold,
                                  cancelled_at_source=o.source_cancelled_at is not None,
                                  applied_945=any(x.applied for x in shipments)),
            payment_gateways=o.payment_gateways, discount_codes=o.discount_codes, tax_lines=o.tax_lines,
            **await self._payments(id),
            dc_number=await self._dc_number(o), ship_to_flags=address_flags(o.ship_to),
            timeline=[TimelineEntry(at=a.at, user_name=a.user_name, entity=a.entity, action=a.action, before=a.before,
                                    after=a.after, reason=a.reason) for a in trail],
        )

    @requires(INVOICE_MANAGE)
    async def set_invoice_prices(self, id: int, body: InvoicePricesIn) -> OrderDetail:
        """The invoice price per order line before the invoice exists (user
        2026-11-03): the draft copies it instead of the PO price. Equal to the PO
        price (or null) means "the PO price". A price that differs from the PO
        needs a reason (as on the invoice), kept in the audit and the log. Once the
        draft exists the invoice is the one place to change it."""
        s = self.uow.session
        o = await self.repo.get(id, for_update=True)
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

    @requires(OPERATE)
    async def resolve_review(self, id: int, tasks, shopify=None) -> ReviewResult:
        barcodes = await self._barcodes(await self.repo.get(id), shopify)
        await self._read_barcodes(id, barcodes)  # Shopify first: a slow store never holds the row lock
        o = await self.repo.get(id, for_update=True)
        if o.fulfillment_status != F.REVIEW:
            raise BusinessError("order.not_in_review", "the order is not in review",
                                {"status": o.fulfillment_status})
        self.uow.set_reason("re-check review")
        remaining = await OrderSync(self.uow, tasks).recheck(o, barcodes)
        return ReviewResult(order=self.to_read(await self.repo.get(id)), remaining=remaining)  # read again: facts expire on flush


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
    @requires(READ)
    async def po_pdf(self, order_id: int) -> tuple[str, bytes]:
        o = await self.repo.get(order_id)
        return f"PO-{o.source_order_number or o.order_number}.pdf", await print_docs.po_pdf(self.uow.session, o)

    @requires(READ)
    async def packet(self, order_id: int) -> tuple[str, bytes]:
        """The order's audit packet: every stored file and the PDFs (FR-OB-55)."""
        o = await self.repo.get(order_id)
        return f"Order-{o.ref or o.order_number}.zip", await print_docs.packet(self.uow.session, o)

    async def packing_list(self, order_id: int, shipment_id: int) -> tuple[str, bytes]:
        """One shipment's packing list (PDF): pallets → items → lots → expiry →
        qty, with tracking, BOL and its 945. Any 945 kept — a shadow-run or
        second one says so on the page."""
        s = self.uow.session
        o = await self.repo.get(order_id)
        sh = (await s.execute(select(Shipment).where(Shipment.id == shipment_id, Shipment.order_id == order_id))).scalar_one_or_none()
        if sh is None:
            raise NotFound("Shipment", shipment_id)
        rows = list((await s.execute(select(ShipmentRow).where(ShipmentRow.shipment_id == sh.id)
                                     .order_by(ShipmentRow.lx_index, ShipmentRow.w12_index))).scalars())
        names = {i.item_code: i.description for i in (await s.execute(select(Item).where(
            Item.item_code.in_({r.item_code for r in rows if r.item_code})))).scalars()} if rows else {}
        st = sh.ship_to or o.ship_to or {}
        ship_to = PdfParty(st.get("name"), [x for x in (st.get("address1"), " ".join(filter(None, (
            st.get("city"), st.get("province"), st.get("zip"))))) if x])
        carrier = (await carriers_by_scac(s, [sh.carrier])).get((sh.carrier or "").strip().upper())
        template = carrier.tracking_url if carrier else None
        note = (None if sh.applied or sh.shadow else "A second 945 for this order: kept, not applied.")
        pdf = render_packing(PackingList(
            number=o.ref, order_number=o.order_number, po=o.source_order_number, ship_date=sh.ship_date, carrier=sh.carrier,
            bol=sh.bol, tracking=list(sh.tracking or []), our_reference=sh.our_reference, document_id=sh.document_id,
            seller=SELLER, ship_to=ship_to, note=note, carrier_name=carrier.name if carrier else None, rows=[PackRow(
                sscc=r.sscc, pallet=r.lx_index, item_code=r.item_code, description=names.get(r.item_code), lot=r.lot,
                expiry=r.expiry, qty_ea=r.qty_shipped_ea, tracking=r.tracking,
                tracking_url=tracking_url(template, r.tracking)) for r in rows]))
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
        check_version(o, version, "this order")
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

