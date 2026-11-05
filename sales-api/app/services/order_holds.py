"""A person stopping or releasing an order (PRD 9.1, FR-OB-18, FR-OB-25, FR-OB-61).

- Cancel order: what the machine lets go to Cancelled, with a reason. A 940 not yet
  sent is cancelled with it; one at the warehouse only through On Hold. It is Sales' own
  record: nothing is written to Shopify, the warehouse or a partner.
- Release hold: Cancelled at Source → Cancelled, Shipped or the status held before;
  945 Conflict → keep the applied 945 (Shipped) or Cancelled. The second 945 stays stored
  and changes no quantity (AC-OB-73). Shipped sends the Shopify write a hold had stopped.
- A 945 row matching no line: linked to a line of the same item, or accepted with a
  reason (a different or substituted item: nothing on the line or the invoice). Once
  none is left the invoice it blocked follows.

Shadow orders are Zoho's: every action here is refused for them.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.domain.order_rules import can_cancel, hold_exits
from app.domain.row_matching import identifiers, line_identifiers
from app.domain.status import FULFILLMENT, INVOICE, OUTBOUND_FILE, Fulfillment as F, Invoice as Inv, OutboundFile
from app.models.config import Item
from app.models.documents import Document, DocumentLink
from app.models.invoices import OrderReturn
from app.models.orders import Order, OrderLine
from app.models.shipments import Shipment, ShipmentRow
from app.platform.authorization import requires
from app.platform.errors import BusinessError
from app.services import oplog
from app.services.invoicing import rows_to_review
from app.services.problems import (
    CANCELLED_AFTER_940, EDI_940_FAILED, EDI_940_REJECTED, EDI_940_TIMEOUT, EDI_945_LINE_UNMATCHED, INVOICE_HOLD, ORDER_ARCHIVED,
    ORDER_REVIEW, SECOND_945,
)
from app.services.shipments import CLOSE, FULFILL, OUTBOX

OPERATE = "sales.order.operate"
STEP = "order"
ORDER_PROBLEMS = (ORDER_REVIEW, ORDER_ARCHIVED, EDI_940_FAILED, EDI_940_REJECTED, EDI_940_TIMEOUT, CANCELLED_AFTER_940, SECOND_945)
RELEASED_BY_THE_ROW = (Inv.PENDING.value, Inv.AWAITING_REQUESTED_DATE.value)  # blocked by advance(), before any confirmation


class OrderActions:
    def __init__(self, uow, tasks) -> None:
        self.uow, self.s, self.tasks = uow, uow.session, tasks

    async def _order(self, id: int, version: int | None = None) -> Order:
        o = await self.s.get(Order, id, with_for_update=True)
        if o is None:
            raise BusinessError("entity.not_found", f"order {id} not found", status_code=404)
        if o.shadow:
            raise BusinessError("order.shadow", "this order belongs to Zoho during the shadow run")
        if version is not None and o.version != version:
            raise BusinessError("entity.stale", "this order was changed by someone else; reload it",
                                {"current_version": o.version, "sent_version": version})
        return o

    async def _resolve(self, order: Order) -> None:
        for event in ORDER_PROBLEMS:
            await self.tasks.resolve_problem(self.s, event, order.id, by=self.uow.actor.user_id)

    async def _cancel(self, o: Order, reason: str) -> None:
        docs = list((await self.s.execute(select(Document).join(DocumentLink, DocumentLink.document_id == Document.id).where(
            DocumentLink.entity_type == "order", DocumentLink.entity_id == o.id, Document.kind == "940_EDI",
            Document.direction == "OUT", Document.file_status.in_((OutboundFile.READY_TO_SEND.value, OutboundFile.SUPPRESSED.value,
                                                                   OutboundFile.SENDING.value))).with_for_update())).scalars())
        if any(d.file_status == OutboundFile.SENDING for d in docs):
            raise BusinessError("order.sending", "its 940 is being sent right now: try again in a moment")
        for d in docs:  # never sent: cancelled with the order (the outbox row then stops at its pre_check)
            OUTBOUND_FILE.transition(d, OutboundFile.CANCELLED, actor=self.uow.actor, events=self.uow.events, reason=f"order cancelled: {reason}")
        FULFILLMENT.transition(o, F.CANCELLED, actor=self.uow.actor, events=self.uow.events, reason=reason)
        o.hold_reason = None
        await self._resolve(o)
        await oplog.write(self.s, "INFO", STEP, f"order {o.label} cancelled: {reason}", order_id=o.id)

    @requires(OPERATE)
    async def cancel(self, id: int, reason: str, version: int | None = None) -> Order:
        o = await self._order(id, version)
        if not can_cancel(o.fulfillment_status, shadow=o.shadow):
            raise BusinessError("order.not_cancellable", f"an order in {o.fulfillment_status} cannot be cancelled here"
                                + (": a 940 at the warehouse is cancelled through On Hold" if o.fulfillment_status in
                                   (F.SENT_940, F.ACCEPTED_940) else ""), {"status": o.fulfillment_status})
        self.uow.set_reason(f"order cancelled: {reason}")
        await self._cancel(o, reason)
        await self.s.flush()
        return o

    async def _applied(self, order_id: int) -> list[Shipment]:
        return list((await self.s.execute(select(Shipment).where(Shipment.order_id == order_id, Shipment.applied.is_(True))
                                          .order_by(Shipment.id).with_for_update())).scalars())

    @requires(OPERATE)
    async def release_hold(self, id: int, to: str, reason: str, version: int | None = None) -> Order:
        o = await self._order(id, version)
        applied = await self._applied(o.id)
        exits = hold_exits(o.fulfillment_status, o.fulfillment_status_before_hold, shadow=o.shadow,
                           cancelled_at_source=o.source_cancelled_at is not None, applied_945=bool(applied))
        if to not in exits:
            raise BusinessError("order.hold_exit", f"this order cannot go to {to} from {o.fulfillment_status}", {"exits": exits})
        self.uow.set_reason(f"hold released to {to}: {reason}")
        if to == F.CANCELLED:
            await self._cancel(o, reason)
        else:
            FULFILLMENT.transition(o, F(to), actor=self.uow.actor, events=self.uow.events, reason=reason)
            await self._resolve(o)
            await oplog.write(self.s, "INFO", STEP, f"order {o.label} released to {to}: {reason}", order_id=o.id)
            if to == F.SHIPPED and o.source_type == "SHOPIFY":
                for sh in applied:  # what the hold stopped: the write goes now, once
                    if sh.shopify_status in ("Not needed", "Suppressed"):
                        sh.shopify_status = "Pending"
                        OUTBOX.add(self.s, CLOSE if sh.shopify_fulfillment_id else FULFILL, {"shipment_id": sh.id}, entity=sh)
        await self.s.flush()
        return o

    @requires(OPERATE)
    async def resolve_row(self, shipment_id: int, row_id: int, order_line_id: int | None, reason: str | None) -> Order:
        row = await self.s.get(ShipmentRow, row_id, with_for_update=True)
        sh = await self.s.get(Shipment, shipment_id, with_for_update=True)
        if row is None or sh is None or row.shipment_id != sh.id or sh.order_id is None:
            raise BusinessError("entity.not_found", "no such 945 row", status_code=404)
        o = await self._order(sh.order_id)
        if not row.needs_review:
            raise BusinessError("shipment.row_resolved", "this 945 row needs no decision")
        why = (reason or "").strip()
        if order_line_id is None and len(why) < 3:
            raise BusinessError("shipment.reason_required", "say why this row is accepted without an order line (a few words)",
                                {"field": "reason"}, status_code=422)
        what = f"accepted: {why}"
        if order_line_id is not None:
            line = await self.s.get(OrderLine, order_line_id, with_for_update=True)
            if line is None or line.order_id != o.id or line.removed:
                raise BusinessError("order.line", f"line {order_line_id} is not an open line of this order")
            item = (await self.s.execute(select(Item).where(Item.item_code == line.item_code))).scalar_one_or_none()
            if not identifiers(row.item_code, row.upc) & line_identifiers(
                    line.item_code, line.source, item.upc if item else None, item.gtin if item else None):
                raise BusinessError("shipment.row_other_item", "this row is another item than that line: accept it with a reason instead")
            row.order_line_id = line.id
            line.shipped_ea = (line.shipped_ea or 0) + row.qty_shipped_ea  # the invoice's quantity comes from here
            what = f"linked to line {line.line_no}" + (f": {why}" if why else "")
        row.needs_review = False
        self.uow.set_reason(f"945 row {row.item_code or row.upc}: {what}")
        # The row has no audit of its own: the shipment carries the decision.
        sh.checks = [*(sh.checks or []), {"check": "row resolved", "message": f"{row.item_code or row.upc}, {row.qty_shipped_ea} EA: {what}"}]
        await oplog.write(self.s, "INFO", STEP, f"order {o.label}: 945 row {row.item_code or row.upc} {what}", order_id=o.id,
                          document_id=sh.document_id)
        await self.s.flush()
        if not await rows_to_review(self.s, o.id):
            await self.tasks.resolve_problem(self.s, EDI_945_LINE_UNMATCHED, sh.id, by=self.uow.actor.user_id)
            returned = (await self.s.execute(select(func.count(OrderReturn.id)).where(OrderReturn.order_id == o.id))).scalar_one()
            if o.invoice_status == Inv.BLOCKED and o.invoice_status_before_hold in RELEASED_BY_THE_ROW and not returned:
                INVOICE.transition(o, Inv(o.invoice_status_before_hold), actor=self.uow.actor, events=self.uow.events,
                                   reason="the 945 row is resolved")
                await self.tasks.resolve_problem(self.s, INVOICE_HOLD, o.id, by=self.uow.actor.user_id)
        await self.s.flush()
        return o
