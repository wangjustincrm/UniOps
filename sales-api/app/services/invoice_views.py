"""The invoicing endpoints' service: permissions (sales.order.operate to confirm
delivery and record a return, sales.invoice.manage to edit, send and release an
invoice, sales.read to look), the list and detail. The work is in
app/services/invoicing.py."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.domain.status import Invoice as Inv
from app.models.config import ItemUnit
from app.models.invoices import DeliveryConfirmation, Invoice, InvoiceLine
from app.models.orders import Order, OrderLine
from app.platform.authorization import authorize, requires
from app.platform.errors import BusinessError
from app.platform.query import FilterRule
from app.platform.repository import Repository
from app.platform.services import ReadOnlyService
from app.platform.views import Preset
from app.schemas.invoices import ConfirmResult, InvoiceDetail, InvoiceLineRead, InvoiceRead

READ = "sales.read"
MANAGE = "sales.invoice.manage"
OPERATE = "sales.order.operate"  # delivery and returns are order work (FR-OB-61)


class InvoiceRepository(Repository[Invoice]):
    model = Invoice
    search_fields = ("invoice_number", "order_ref_sql", "partner_name_sql", "delivery_number")
    suggestable = ("invoice_number", "order_ref", "partner_name")
    search_numbers = ("order_number_sql",)  # the Internal no., matched exactly
    list_fields = ("id", "invoice_number", "order_id", "delivery_number", "method",
                   "invoice_date", "currency", "total", "sent_at", "send_count", "error", "created_at")
    sortable = {"order_ref": Invoice.order_ref_sql, "order_number": Invoice.order_number_sql, "partner_name": Invoice.partner_name_sql}
    filterable = {"order_ref": FilterRule(Invoice.order_ref_sql, kind="text"),
                  "order_number": FilterRule(Invoice.order_number_sql, kind="number", cast=int),
                  "partner_name": FilterRule(Invoice.partner_name_sql, kind="text")}
    labels = {"invoice_number": "Invoice", "order_ref": "Order ref", "order_number": "Internal no.", "partner_name": "Partner",
              "delivery_number": "Delivery no.", "invoice_date": "Invoice date", "total": "Amount", "send_count": "Times sent"}
    enums = {"method": ("810_JSON", "EMAIL", "NONE")}
    default_sort = ("-invoice_date", "-id")
    groupable = ("partner_name", "method", "currency")
    summable = ("total",)
    sum_currency = "currency"
    presets = (Preset("all", "All"),
               Preset("not_sent", "Not sent", {"filters": [{"field": "sent_at", "op": "null", "value": None}]}),
               Preset("failed", "Failed", {"filters": [{"field": "error", "op": "not_null", "value": None}]}))


async def po_prices(s, order_line_ids: list[int]) -> dict[int, Decimal]:
    """The PO price per invoice unit (CASE) of each order line — the price the
    invoice was built with (A.9): 850 price × CASE factor ÷ the order unit's factor."""
    if not order_line_ids:
        return {}
    lines = list((await s.execute(select(OrderLine).where(OrderLine.id.in_(order_line_ids)))).scalars())
    cases = {u.item_code: u.factor_ea for u in (await s.execute(select(ItemUnit).where(
        ItemUnit.item_code.in_([ln.item_code for ln in lines if ln.item_code]), ItemUnit.unit == "CASE"))).scalars()}
    out = {}
    for ln in lines:
        case, factor = cases.get(ln.item_code), ln.factor_ea
        if ln.unit_price is not None and case and factor:
            out[ln.id] = (Decimal(ln.unit_price) * Decimal(case) / Decimal(factor)).quantize(Decimal("0.00001"))
    return out


class InvoiceService(ReadOnlyService[Invoice, InvoiceRead]):
    repo_cls = InvoiceRepository
    read_schema = InvoiceRead
    read_permission = READ

    async def _with_order(self, items: list[InvoiceRead]) -> list[InvoiceRead]:
        ids = {i.order_id for i in items}
        orders = {o.id: o for o in (await self.uow.session.execute(select(Order).where(Order.id.in_(ids)))).scalars()} if ids else {}
        for i in items:
            o = orders.get(i.order_id)
            if o is not None:
                i.order_number, i.partner_code, i.po, i.status = o.order_number, o.partner_code, o.source_order_number, o.invoice_status
        return items

    async def list(self, spec):
        page = await super().list(spec)
        await self._with_order(page.items)
        return page

    @requires(READ)
    async def detail(self, id: int) -> InvoiceDetail:
        s = self.uow.session
        inv = await self.repo.get(id)
        [base] = await self._with_order([self.to_read(inv)])
        lines = (await s.execute(select(InvoiceLine).where(InvoiceLine.invoice_id == id).order_by(InvoiceLine.line_no))).scalars()
        confirmed = (await s.execute(select(DeliveryConfirmation.confirmed_date).where(
            DeliveryConfirmation.order_id == inv.order_id))).scalar_one_or_none()
        lines = list(lines)
        po = await po_prices(s, [x.order_line_id for x in lines if x.order_line_id])
        return InvoiceDetail(**base.model_dump(), confirmed_date=confirmed, lines=[
            InvoiceLineRead.model_validate(x).model_copy(update={"po_price": po.get(x.order_line_id)}) for x in lines])

    @requires(OPERATE)
    async def confirm(self, invoicing, body) -> InvoiceDetail:
        if body.send:
            await authorize(self.uow, MANAGE)  # sending is invoice work: both permissions, before anything changes
        inv = await invoicing.confirm(self.uow, body.order_id, confirmed_date=body.confirmed_date,
                                      delivered={x.order_line_id: x.delivered_qty for x in body.lines}, note=body.note)
        if body.send:
            await self._send_if_ready(invoicing, inv)
        return await self._detail_after(inv.id)

    async def _send_if_ready(self, invoicing, inv) -> bool:
        """A Blocked invoice waits for a person, as after a plain confirmation."""
        order = await self.uow.session.get(Order, inv.order_id)
        if order.invoice_status != Inv.READY.value or inv.method == "NONE":
            return False
        await invoicing.send(self.uow, inv.id)
        return True

    @requires(OPERATE)
    async def confirm_bulk(self, invoicing, body) -> list[ConfirmResult]:
        """FR-OB-31: in bulk, delivered = shipped. Each order on its own: one
        that cannot be confirmed does not stop the others."""
        if body.send:
            await authorize(self.uow, MANAGE)
        out = []
        for oid in body.order_ids:
            try:
                async with self.uow.session.begin_nested():
                    inv = await invoicing.confirm(self.uow, oid, confirmed_date=body.confirmed_date)
                    sent = body.send and await self._send_if_ready(invoicing, inv)
                status = (await self.uow.session.get(Order, oid)).invoice_status
                out.append(ConfirmResult(order_id=oid, ok=True, invoice_id=inv.id, status=status, sent=sent))
            except BusinessError as e:
                out.append(ConfirmResult(order_id=oid, ok=False, error=e.message))
        return out

    @requires(MANAGE)
    async def edit(self, invoicing, id: int, body, version: int | None) -> InvoiceDetail:
        extra = {"delivery_number": body.delivery_number} if "delivery_number" in body.model_fields_set else {}
        await invoicing.edit(self.uow, id, version=version, invoice_date=body.invoice_date, reason=body.reason,
                             lines=[x.model_dump(exclude_none=True) for x in body.lines], **extra)
        return await self._detail_after(id)

    @requires(MANAGE)
    async def send(self, invoicing, id: int) -> InvoiceDetail:
        await invoicing.send(self.uow, id)
        return await self._detail_after(id)

    @requires(MANAGE)
    async def release(self, invoicing, body) -> dict:
        o = await invoicing.release(self.uow, body.order_id, body.reason)
        return {"order_id": o.id, "invoice_status": o.invoice_status}

    @requires(OPERATE)
    async def record_return(self, invoicing, body) -> dict:
        r = await invoicing.record_return(self.uow, body.order_id, kind=body.kind, reason=body.reason,
                                          lines=[x.model_dump() for x in body.lines])
        o = await self.uow.session.get(Order, body.order_id)
        return {"return_id": r.id, "invoice_status": o.invoice_status}

    async def _detail_after(self, id: int) -> InvoiceDetail:
        await self.uow.session.flush()
        return await InvoiceService.detail.__wrapped__(self, id)  # already authorised
