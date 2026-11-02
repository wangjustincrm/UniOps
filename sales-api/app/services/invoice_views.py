"""The invoicing endpoints' service: permissions (sales.invoice.manage for
every change, sales.read to look), the list and detail. The work is in
app/services/invoicing.py."""
from __future__ import annotations

from sqlalchemy import select

from app.models.invoices import DeliveryConfirmation, Invoice, InvoiceLine
from app.models.orders import Order
from app.platform.authorization import requires
from app.platform.errors import BusinessError
from app.platform.repository import Repository
from app.platform.services import ReadOnlyService
from app.platform.views import Preset
from app.schemas.invoices import ConfirmResult, InvoiceDetail, InvoiceLineRead, InvoiceRead

READ = "sales.read"
MANAGE = "sales.invoice.manage"


class InvoiceRepository(Repository[Invoice]):
    model = Invoice
    search_fields = ("invoice_number",)
    list_fields = ("id", "invoice_number", "order_id", "method", "invoice_date", "currency", "total", "sent_at",
                   "send_count", "error", "created_at")
    labels = {"invoice_number": "Invoice", "invoice_date": "Invoice date", "send_count": "Times sent"}
    enums = {"method": ("810_JSON", "EMAIL", "NONE")}
    default_sort = ("-invoice_date", "-id")
    groupable = ("method", "currency")
    presets = (Preset("all", "All"),
               Preset("not_sent", "Not sent", {"filters": [{"field": "sent_at", "op": "null", "value": None}]}),
               Preset("failed", "Failed", {"filters": [{"field": "error", "op": "not_null", "value": None}]}))


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
        return InvoiceDetail(**base.model_dump(), lines=[InvoiceLineRead.model_validate(x) for x in lines],
                             confirmed_date=confirmed)

    @requires(MANAGE)
    async def confirm(self, invoicing, body) -> InvoiceDetail:
        inv = await invoicing.confirm(self.uow, body.order_id, confirmed_date=body.confirmed_date,
                                      delivered={x.order_line_id: x.delivered_qty for x in body.lines}, note=body.note)
        return await self._detail_after(inv.id)

    @requires(MANAGE)
    async def confirm_bulk(self, invoicing, body) -> list[ConfirmResult]:
        """FR-OB-31: in bulk, delivered = shipped. Each order on its own: one
        that cannot be confirmed does not stop the others."""
        out = []
        for oid in body.order_ids:
            try:
                async with self.uow.session.begin_nested():
                    inv = await invoicing.confirm(self.uow, oid, confirmed_date=body.confirmed_date)
                status = (await self.uow.session.get(Order, oid)).invoice_status
                out.append(ConfirmResult(order_id=oid, ok=True, invoice_id=inv.id, status=status))
            except BusinessError as e:
                out.append(ConfirmResult(order_id=oid, ok=False, error=e.message))
        return out

    @requires(MANAGE)
    async def edit(self, invoicing, id: int, body, version: int | None) -> InvoiceDetail:
        await invoicing.edit(self.uow, id, version=version, invoice_date=body.invoice_date,
                             lines=[x.model_dump(exclude_none=True) for x in body.lines])
        return await self._detail_after(id)

    @requires(MANAGE)
    async def send(self, invoicing, id: int) -> InvoiceDetail:
        await invoicing.send(self.uow, id)
        return await self._detail_after(id)

    @requires(MANAGE)
    async def release(self, invoicing, body) -> dict:
        o = await invoicing.release(self.uow, body.order_id, body.reason)
        return {"order_id": o.id, "invoice_status": o.invoice_status}

    @requires(MANAGE)
    async def record_return(self, invoicing, body) -> dict:
        r = await invoicing.record_return(self.uow, body.order_id, kind=body.kind, reason=body.reason,
                                          lines=[x.model_dump() for x in body.lines])
        o = await self.uow.session.get(Order, body.order_id)
        return {"return_id": r.id, "invoice_status": o.invoice_status}

    async def _detail_after(self, id: int) -> InvoiceDetail:
        await self.uow.session.flush()
        return await InvoiceService.detail.__wrapped__(self, id)  # already authorised
