"""Delivery confirmation and invoices (FR-OB-19, 30 … 35; P6).

- `advance` (each cycle): a Shipped order's invoice moves Pending → Awaiting
  Requested Date → Waiting for Delivery Confirmation when its requested
  delivery date is reached (Walmart, dc routing: do-not-deliver-after); no
  date → at once. A lot exception (a 945 row that matches no order line,
  FR-OB-25) holds it (Blocked) with a task.
- `confirm`: a person confirms delivery with the delivered quantity per line
  (≤ shipped; default = shipped) — the confirmed date is the invoice date;
  the invoice is built (cases at the 850 price) and is Ready. A part case, no
  CASE unit or no price holds it (Blocked) with a task: never invoiced
  silently short. `release` returns it to Ready after a person decided.
- `send` / resend: one outbox row (`send_invoice`): 810 JSON put to the
  invoice connection through the guarded SFTP path, or an email with the
  invoice, PO and packing-list PDFs to the partner's invoice address, the
  internal copy in Cc (FR-OB-33/34). Never for a shadow order, never while the
  shadow run is on (pre_check: the second barrier).
- `record_return`: cancelled or returned after shipment (FR-OB-19) — recorded
  with quantities and reason; the invoice is held (Blocked) and a task raised.
Nothing is invoiced without a confirmation; nothing escalates.
"""
from __future__ import annotations

import hashlib
import html
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select

from app.domain.invoicing import CASE_UNIT, Header810, LineIn, LineOut, amount, build_810, build_lines
from app.domain.status import INVOICE
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.integrations.email import ExternalEmail
from app.models.config import Connection, Item, ItemUnit, Partner
from app.models.documents import Document, DocumentLink
from app.models.invoices import DeliveryConfirmation, Invoice, InvoiceLine, OrderReturn
from app.models.orders import Order, OrderLine
from app.models.records import OutboxRecord
from app.models.shipments import Shipment, ShipmentRow
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.outbox import ONCE, Outbox, OutboxKind
from app.services import oplog
from app.services.intake import ConnectionSnapshot
from app.services.invoice_pdf import Party, PdfDoc, PdfLine, render
from app.services.outbound import TORONTO
from app.services.problems import INVOICE_FAILED, INVOICE_HOLD, ORDER_RETURN, problem
from app.services.settings import INVOICE_CONNECTION_ID, SETTINGS, SHADOW_MODE

logger = logging.getLogger(__name__)

STEP = "invoice"
SEND_INVOICE = "send_invoice"
OUTBOX = Outbox(OutboxRecord)
SELLER = Party("Canada Royal Milk ULC", [])
EDITABLE = (Inv.READY, Inv.FAILED, Inv.SENT, Inv.BLOCKED)


@dataclass
class AdvanceReport:
    waiting: int = 0
    awaiting: int = 0
    held: int = 0
    skipped: str | None = None
    detail: list[str] = field(default_factory=list)


async def _shadow(s) -> bool:
    return bool(await SETTINGS.get(s, SHADOW_MODE))


class Invoicing:
    def __init__(self, *, uow_factory, tasks, sftp, email,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.uow_factory = uow_factory
        self.tasks = tasks
        self.sftp = sftp
        self.email = email
        self.clock = clock

    def today(self) -> date:
        return self.clock().astimezone(TORONTO).date()

    # ── the worklist (FR-OB-31) ──────────────────────────────────────────────
    async def advance(self, *, run_id: int | None = None) -> AdvanceReport:
        report = AdvanceReport()
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            s = uow.session
            if await _shadow(s):
                report.skipped = "shadow run: nothing is invoiced"
                return report
            rows = (await s.execute(select(Order, Partner).join(Partner, Partner.code == Order.partner_code).where(
                Order.fulfillment_status == F.SHIPPED.value, Order.shadow.is_(False),
                Order.invoice_status.in_((Inv.PENDING.value, Inv.AWAITING_REQUESTED_DATE.value)))
                .order_by(Order.id).with_for_update(of=Order))).all()
            today = self.today()
            for order, partner in rows:
                review = (await s.execute(select(ShipmentRow.item_code).join(Shipment, Shipment.id == ShipmentRow.shipment_id)
                                          .where(Shipment.order_id == order.id, Shipment.applied.is_(True),
                                                 ShipmentRow.needs_review.is_(True)))).scalars().all()
                if review:
                    await self._hold(uow, order, "a 945 line matches no order line (FR-OB-25: lot exception)",
                                     f"945 item(s) {', '.join(x or '?' for x in review)} match no line of order "
                                     f"{order.order_number}. Decide (correct the order or the shipment), then Release.")
                    report.held += 1
                    continue
                due = order.do_not_deliver_after if partner.dc_routing_required else order.requested_delivery_date
                target = Inv.WAITING_FOR_DELIVERY_CONFIRMATION if due is None or due <= today else Inv.AWAITING_REQUESTED_DATE
                if target.value == order.invoice_status:
                    continue
                INVOICE.transition(order, target, actor=uow.actor, events=uow.events,
                                   reason="shipped; the requested delivery date is reached" if target ==
                                   Inv.WAITING_FOR_DELIVERY_CONFIRMATION else "shipped; waiting for the requested delivery date")
                if target == Inv.WAITING_FOR_DELIVERY_CONFIRMATION:
                    report.waiting += 1
                else:
                    report.awaiting += 1
            await uow.commit()
        return report

    async def _hold(self, uow, order: Order, reason: str, description: str) -> None:
        if order.invoice_status != Inv.BLOCKED.value:
            INVOICE.transition(order, Inv.BLOCKED, actor=uow.actor, events=uow.events, reason=reason)
        await self.tasks.raise_problem(uow.session, INVOICE_HOLD, order.id, problem(
            INVOICE_HOLD, f"Invoice on hold: order {order.order_number}", description))

    async def _refuse_shadow(self, s, order: Order) -> None:
        if await _shadow(s):
            raise BusinessError("invoice.shadow", "the shadow run is on: nothing is invoiced (Zoho is live)")
        if order.shadow:
            raise BusinessError("invoice.shadow_order", "this order was received during the shadow run: Zoho invoices it")

    # ── confirmation and the invoice ────────────────────────────────────────
    async def confirm(self, uow, order_id: int, *, confirmed_date: date, delivered: dict[int, int] | None = None,
                      note: str | None = None) -> Invoice:
        s = uow.session
        order = await s.get(Order, order_id, with_for_update=True)
        if order is None:
            raise BusinessError("order.not_found", "no such order", status_code=404)
        await self._refuse_shadow(s, order)
        if order.invoice_status != Inv.WAITING_FOR_DELIVERY_CONFIRMATION.value:
            raise BusinessError("delivery.not_waiting", f"the order's invoice is {order.invoice_status}, not waiting for "
                                "delivery confirmation", {"status": order.invoice_status})
        if confirmed_date > self.today():
            raise BusinessError("delivery.future", "a delivery cannot be confirmed for a future date")
        lines = list((await s.execute(select(OrderLine).where(OrderLine.order_id == order.id, OrderLine.removed.is_(False))
                                      .order_by(OrderLine.line_no).with_for_update())).scalars())
        delivered = delivered or {}
        for ln in lines:
            if ln.shipped_ea is None:
                continue
            want = delivered.get(ln.id, ln.shipped_ea)
            if want < 0 or want > ln.shipped_ea:
                raise BusinessError("delivery.quantity", f"line {ln.line_no}: delivered {want} EA must be 0 … {ln.shipped_ea} "
                                    "(the shipped quantity)", {"line_id": ln.id})
            ln.delivered_qty = want
        uow.set_reason("delivery confirmed")
        s.add(DeliveryConfirmation(order_id=order.id, confirmed_date=confirmed_date, note=note, version=1))
        inv, problems = await self._build(s, order, lines, confirmed_date)
        INVOICE.transition(order, Inv.READY, actor=uow.actor, events=uow.events, reason="delivery confirmed (FR-OB-31)")
        if problems:
            await self._hold(uow, order, "the invoice needs a decision", f"Invoice {inv.invoice_number}: " + "; ".join(problems)
                             + ". Edit the invoice if needed, then Release it.")
        await s.flush()
        return inv

    async def _build(self, s, order: Order, lines: list[OrderLine], when: date) -> tuple[Invoice, list[str]]:
        partner = (await s.execute(select(Partner).where(Partner.code == order.partner_code))).scalar_one()
        codes = [ln.item_code for ln in lines if ln.item_code]
        items = {i.item_code: i for i in (await s.execute(select(Item).where(Item.item_code.in_(codes)))).scalars()}
        cases = dict((await s.execute(select(ItemUnit.item_code, ItemUnit.factor_ea).where(
            ItemUnit.item_code.in_(codes), ItemUnit.unit == CASE_UNIT, ItemUnit.active.is_(True)))).all())
        built = build_lines([LineIn(
            order_line_id=ln.id, line_no=ln.line_no, item_code=ln.item_code, buyer_part=(ln.source or {}).get("buyer_part"),
            upc=items[ln.item_code].upc if ln.item_code in items else None,
            gtin=items[ln.item_code].gtin if ln.item_code in items else None,
            description=items[ln.item_code].description if ln.item_code in items else None,
            delivered_ea=int(ln.delivered_qty or 0), case_factor=int(cases[ln.item_code]) if ln.item_code in cases else None,
            order_factor=ln.factor_ea, unit_price=ln.unit_price) for ln in lines if ln.delivered_qty])
        inv = Invoice(invoice_number=f"IN-{order.order_number}", order_id=order.id, method=partner.invoice_method,
                      invoice_date=when, currency=order.currency or "CAD", payment_terms=partner.payment_terms,
                      subtotal=built.subtotal, total=built.subtotal, version=1, send_count=0)
        s.add(inv)
        await s.flush()
        for ln in built.lines:
            s.add(InvoiceLine(invoice_id=inv.id, line_no=ln.line_no, order_line_id=ln.order_line_id, item_code=ln.item_code,
                              buyer_part=ln.buyer_part, description=ln.description, uom=ln.uom, qty=ln.qty,
                              unit_price=ln.unit_price, amount=ln.amount))
        return inv, built.problems

    async def edit(self, uow, invoice_id: int, *, version: int | None, invoice_date: date | None,
                   lines: list[dict[str, Any]]) -> Invoice:
        """FR-OB-35: quantities, unit prices and the date, before it is sent —
        or a correction after (then Send again). Every change is audited."""
        s = uow.session
        inv = await s.get(Invoice, invoice_id, with_for_update=True)
        if inv is None:
            raise BusinessError("invoice.not_found", "no such invoice", status_code=404)
        if version is not None and inv.version != version:
            raise BusinessError("entity.stale", "someone else changed this invoice", status_code=409)
        order = await s.get(Order, inv.order_id, with_for_update=True)
        if Inv(order.invoice_status) not in EDITABLE:
            raise BusinessError("invoice.not_editable", f"the invoice is {order.invoice_status}")
        mine = {ln.id: ln for ln in (await s.execute(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id))).scalars()}
        uow.set_reason("invoice edited")
        for change in lines:
            ln = mine.get(int(change["id"]))
            if ln is None:
                raise BusinessError("invoice.line", f"line {change['id']} is not on this invoice")
            if "qty" in change:
                if int(change["qty"]) < 0:
                    raise BusinessError("invoice.line", "a quantity cannot be negative")
                ln.qty = int(change["qty"])
            if "unit_price" in change:
                price = Decimal(str(change["unit_price"]))
                if price < 0:
                    raise BusinessError("invoice.line", "a unit price cannot be negative")
                ln.unit_price = price
            ln.amount = amount(ln.qty, ln.unit_price)
        if invoice_date is not None:
            inv.invoice_date = invoice_date
        inv.subtotal = inv.total = sum((ln.amount for ln in mine.values()), Decimal(0))
        await s.flush()
        return inv

    async def release(self, uow, order_id: int, reason: str) -> Order:
        s = uow.session
        order = await s.get(Order, order_id, with_for_update=True)
        if order is None or order.invoice_status != Inv.BLOCKED.value:
            raise BusinessError("invoice.not_blocked", "the invoice is not on hold")
        uow.set_reason(f"invoice released: {reason}")
        INVOICE.transition(order, Inv(order.invoice_status_before_hold), actor=uow.actor, events=uow.events,
                           reason=f"released: {reason}")
        await self.tasks.resolve_problem(s, INVOICE_HOLD, order.id, by=uow.actor.user_id)
        await s.flush()
        return order

    async def send(self, uow, invoice_id: int) -> Invoice:
        s = uow.session
        inv = await s.get(Invoice, invoice_id, with_for_update=True)
        if inv is None:
            raise BusinessError("invoice.not_found", "no such invoice", status_code=404)
        order = await s.get(Order, inv.order_id, with_for_update=True)
        await self._refuse_shadow(s, order)
        status = Inv(order.invoice_status)
        if status not in (Inv.READY, Inv.FAILED, Inv.SENT):
            raise BusinessError("invoice.not_sendable", f"the invoice is {status.value}", {"status": status.value})
        if inv.method == "NONE":
            raise BusinessError("invoice.method_none", "this partner is not invoiced (method None)")
        uow.set_reason("invoice resend" if status == Inv.SENT else "invoice send")
        if status != Inv.SENT:
            INVOICE.transition(order, Inv.QUEUED, actor=uow.actor, events=uow.events, reason="invoice queued")
        OUTBOX.add(s, SEND_INVOICE, {"invoice_id": inv.id}, entity=inv)
        await s.flush()
        return inv

    async def record_return(self, uow, order_id: int, *, kind: str, reason: str, lines: list[dict[str, int]]) -> OrderReturn:
        s = uow.session
        order = await s.get(Order, order_id, with_for_update=True)
        if order is None:
            raise BusinessError("order.not_found", "no such order", status_code=404)
        if order.fulfillment_status not in (F.SHIPPED.value, F.ON_HOLD.value):
            raise BusinessError("return.not_shipped", "only an order that shipped can be cancelled or returned after shipment")
        known = {ln.id: ln for ln in (await s.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars()}
        for x in lines:
            ln = known.get(int(x["order_line_id"]))
            if ln is None or int(x["qty_ea"]) <= 0 or int(x["qty_ea"]) > int(ln.shipped_ea or 0):
                raise BusinessError("return.quantity", "each line must be on the order, with 1 … the shipped quantity")
        uow.set_reason(f"{kind}: {reason}")
        rec = OrderReturn(order_id=order.id, kind=kind, reason=reason, lines=lines)
        s.add(rec)
        stop = Inv(order.invoice_status) in INVOICE.holds[Inv.BLOCKED]
        if stop:
            INVOICE.transition(order, Inv.BLOCKED, actor=uow.actor, events=uow.events, reason=f"{kind} (FR-OB-19)")
        desc = ", ".join(f"line {known[int(x['order_line_id'])].line_no}: {x['qty_ea']} EA" for x in lines)
        await self.tasks.raise_problem(s, ORDER_RETURN, order.id, problem(
            ORDER_RETURN, f"{kind}: order {order.order_number}",
            f"Order {order.order_number} ({order.partner_code} {order.source_order_number}): {kind.lower()} — {desc}. "
            f"Reason: {reason}. " + ("The invoice is on hold: decide on a credit or a corrected invoice, then Release."
                                    if stop else "The invoice was not affected.")))
        await s.flush()
        return rec

    # ── sending (outbox) ─────────────────────────────────────────────────────
    def outbox_kind(self) -> OutboxKind:
        return OutboxKind(effect=self._effect, policy=ONCE, pre_check=self._pre_check, on_done=self._done,
                          on_failed=self._failed, on_suppressed=self._suppressed)

    async def _pre_check(self, payload: dict[str, Any], uow) -> str | None:
        s = uow.session
        inv = await s.get(Invoice, payload["invoice_id"], with_for_update=True)
        order = await s.get(Order, inv.order_id, with_for_update=True) if inv else None
        if inv is None or order is None:
            return "the invoice or its order is gone"
        if await _shadow(s) or order.shadow:  # the second barrier
            await oplog.write(s, "WARNING", STEP, f"shadow run: invoice {inv.invoice_number} not sent", order_id=order.id)
            if order.invoice_status == Inv.QUEUED.value:
                INVOICE.transition(order, Inv.FAILED, actor=uow.actor, events=uow.events, reason="not sent: shadow run")
            inv.error = "not sent: the shadow run"
            return "the shadow run: nothing is invoiced"
        if order.invoice_status not in (Inv.QUEUED.value, Inv.SENT.value):
            return f"the invoice is {order.invoice_status}"
        return None

    async def _effect(self, payload: dict[str, Any], ctx) -> None:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            s = uow.session
            inv = await s.get(Invoice, payload["invoice_id"])
            order = await s.get(Order, inv.order_id)
            partner = (await s.execute(select(Partner).where(Partner.code == order.partner_code))).scalar_one()
            lines = list((await s.execute(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)
                                          .order_by(InvoiceLine.line_no))).scalars())
            if inv.method == "810_JSON":
                conn_id = await SETTINGS.get(s, INVOICE_CONNECTION_ID)
                conn = await s.get(Connection, conn_id) if conn_id else None
                if conn is None or conn.kind != "SFTP" or not conn.active:
                    raise RuntimeError("no active invoice connection is set (setting invoice.connection_id)")
                snap = ConnectionSnapshot.of(conn)
                codes = [ln.item_code for ln in lines if ln.item_code]
                items = {i.item_code: i for i in (await s.execute(select(Item).where(Item.item_code.in_(codes)))).scalars()}
                data = build_810(Header810(order.partner_code, order.order_number, order.source_order_number,
                                           inv.invoice_date, inv.payment_terms, inv.currency),
                                 [LineOut(line_no=ln.line_no, order_line_id=ln.order_line_id, item_code=ln.item_code,
                                          buyer_part=ln.buyer_part, description=ln.description, uom=ln.uom, qty=ln.qty,
                                          unit_price=ln.unit_price, amount=ln.amount,
                                          upc=items[ln.item_code].upc if ln.item_code in items else None,
                                          gtin=items[ln.item_code].gtin if ln.item_code in items else None) for ln in lines])
                name = (f"810-{partner.abbrev}-{order.order_number}-{order.source_order_number or ''}-"
                        f"{self.clock().astimezone(TORONTO):%Y%m%d_%H%M%S}.json")
                await self._document(s, inv, order, data, name, system="TRUECOMMERCE", kind="810_JSON", conn_id=conn.id)
                await uow.commit()
                folder = snap.test_outbound_folder if snap.mode == "TEST" else snap.outbound_folder
                if not folder:
                    raise RuntimeError(f"connection {snap.name} has no {'test ' if snap.mode == 'TEST' else ''}outbound folder")
                await self.sftp.put(snap, folder, name, data)  # guarded: switch + LIVE + write host
                return
            if inv.method == "EMAIL":
                if not partner.invoice_email:
                    raise RuntimeError(f"partner {partner.code} has no invoice email address")
                msg, pdf = await self._email(s, inv, order, partner, lines)
                await self._document(s, inv, order, pdf, msg.subject, system="EMAIL", kind="INVOICE_EMAIL", conn_id=None)
                await uow.commit()
                await self.email.send_external(msg)  # guarded: SALES_EXTERNAL_EMAIL_ENABLED
                return
            raise RuntimeError(f"invoice method {inv.method} sends nothing")

    async def _document(self, s, inv: Invoice, order: Order, data: bytes, name: str, *, system: str, kind: str,
                        conn_id: int | None) -> Document:
        sha = hashlib.sha256(data).hexdigest()
        doc = (await s.execute(select(Document).where(Document.sha256 == sha, Document.content.is_not(None)))).scalar_one_or_none()
        if doc is None:  # an identical resend keeps the document it already has
            doc = Document(direction="OUT", origin="UNIOPS", system=system, kind=kind, remote_name=name[:500], size=len(data),
                           sha256=sha, content=data, partner_code=order.partner_code, connection_id=conn_id)
            s.add(doc)
            await s.flush()
            s.add(DocumentLink(document_id=doc.id, entity_type="order", entity_id=order.id))
        inv.document_id = doc.id
        return doc

    async def _email(self, s, inv: Invoice, order: Order, partner: Partner, lines: list[InvoiceLine]):
        st = order.ship_to or {}
        bt = order.bill_to or st
        def party(a: dict) -> Party:
            return Party(a.get("name"), [x for x in (a.get("address1"), " ".join(filter(None, (a.get("city"), a.get("province"),
                                                                                        a.get("zip"))))) if x])
        olines = list((await s.execute(select(OrderLine).where(OrderLine.order_id == order.id, OrderLine.removed.is_(False))
                                       .order_by(OrderLine.line_no))).scalars())
        rows = (await s.execute(select(ShipmentRow).join(Shipment, Shipment.id == ShipmentRow.shipment_id).where(
            Shipment.order_id == order.id, Shipment.applied.is_(True)).order_by(ShipmentRow.lx_index, ShipmentRow.w12_index))).scalars().all()
        common = dict(po=order.source_order_number, order_number=order.order_number, seller=SELLER, buyer=party(bt),
                      ship_to=party(st))
        invoice_pdf = render(PdfDoc(title="Invoice", number=inv.invoice_number, when=inv.invoice_date, terms=inv.payment_terms,
                                    currency=inv.currency, total=inv.total, lines=[
                                        PdfLine(ln.item_code, ln.description, ln.qty, ln.uom, ln.unit_price, ln.amount) for ln in lines],
                                    **common))
        po_pdf = render(PdfDoc(title="Purchase order", number=order.source_order_number or str(order.order_number),
                               when=order.order_date, lines=[PdfLine(ln.item_code, (ln.source or {}).get("buyer_part"),
                                                                     f"{ln.ordered_qty.normalize():f}", ln.order_unit or "EA",
                                                                     ln.unit_price, None) for ln in olines], **common))
        packing_pdf = render(PdfDoc(title="Packing list", number=str(order.order_number), when=inv.invoice_date, priced=False,
                                    lines=[PdfLine(r.item_code, None, r.qty_shipped_ea, "EA",
                                                   extra=" / ".join(x for x in (r.lot, r.sscc) if x)) for r in rows], **common))
        po = order.source_order_number or ""
        subject = f"Order Documents | {partner.name} | {order.order_number} | {po}"  # A.12
        esc = html.escape
        text = (f"Please find attached invoice {inv.invoice_number} for your PO {po} (order {order.order_number}), "
                f"with the purchase order and the packing list.\nTotal: {inv.currency} {inv.total:.2f}\n")
        body_html = (f"<p>Please find attached invoice <b>{esc(inv.invoice_number)}</b> for your PO <b>{esc(po)}</b> "
                     f"(order {esc(str(order.order_number))}), with the purchase order and the packing list.</p>"
                     f"<p>Total: {esc(inv.currency)} {inv.total:.2f}</p>")
        cc = tuple(x.strip() for x in (partner.invoice_cc or "").replace(";", ",").split(",") if x.strip())
        msg = ExternalEmail(to=(partner.invoice_email,), cc=cc, subject=subject, body_text=text, body_html=body_html,
                            attachments=((f"{inv.invoice_number}.pdf", invoice_pdf, "application/pdf"),
                                         (f"PO-{po or order.order_number}.pdf", po_pdf, "application/pdf"),
                                         (f"PackingList-{order.order_number}.pdf", packing_pdf, "application/pdf")))
        return msg, invoice_pdf

    async def _pair(self, row, uow):
        inv = await uow.session.get(Invoice, row.payload["invoice_id"], with_for_update=True)
        return inv, await uow.session.get(Order, inv.order_id, with_for_update=True)

    async def _done(self, row, exc, uow) -> None:
        inv, order = await self._pair(row, uow)
        if order.invoice_status == Inv.QUEUED.value:
            INVOICE.transition(order, Inv.SENT, actor=uow.actor, events=uow.events, reason=f"invoice {inv.method} sent")
        inv.sent_at, inv.send_count, inv.error = self.clock(), inv.send_count + 1, None  # the invoice row is the send record
        await self.tasks.resolve_problem(uow.session, INVOICE_FAILED, order.id)

    async def _failed(self, row, exc, uow) -> None:
        inv, order = await self._pair(row, uow)
        inv.error = str(exc)[:2000]
        if order.invoice_status == Inv.QUEUED.value:
            INVOICE.transition(order, Inv.FAILED, actor=uow.actor, events=uow.events, reason=str(exc)[:200])
        await self.tasks.raise_problem(uow.session, INVOICE_FAILED, order.id, problem(
            INVOICE_FAILED, f"Invoice not sent: {inv.invoice_number}",
            f"Invoice {inv.invoice_number} (order {order.order_number}, {inv.method}) was not sent: {exc}. "
            "It is not retried automatically — fix the cause, then Send again."))

    async def _suppressed(self, row, exc, uow) -> None:
        inv, order = await self._pair(row, uow)
        inv.error = f"not sent: {exc}"[:2000]
        if order.invoice_status == Inv.QUEUED.value:
            INVOICE.transition(order, Inv.FAILED, actor=uow.actor, events=uow.events, reason="refused by the outbound guard")
        await oplog.write(uow.session, "INFO", STEP, f"invoice {inv.invoice_number} refused by the guard: {exc}", order_id=order.id)

