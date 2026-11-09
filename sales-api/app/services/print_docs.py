"""The documents a person prints or downloads (FR-OB-55): the purchase order, the invoice, the
packing list of everything shipped, the transfer order, and an order's audit packet (every stored
file of the order with these PDFs, in one zip). The same PDFs go with an emailed invoice."""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date

from sqlalchemy import select

from app.models.config import Warehouse
from app.models.documents import Document, DocumentLink
from app.models.invoices import Invoice, InvoiceLine
from app.models.orders import Order, OrderLine
from app.models.shipments import Shipment, ShipmentRow
from app.models.transfers import Transfer, TransferLine
from app.services.invoice_pdf import Party, PdfDoc, PdfLine, render

SELLER = Party("Canada Royal Milk ULC", [])


def _party(a: dict | None) -> Party:
    a = a or {}
    place = " ".join(filter(None, (a.get("city"), a.get("province"), a.get("zip"))))
    return Party(a.get("name"), [x for x in (a.get("address1"), place) if x])


def _common(order: Order, delivery_number: str | None = None) -> dict:
    st = order.ship_to or {}
    return dict(po=order.source_order_number, order_number=order.order_number, order_ref=order.source_reference,
                delivery_number=delivery_number, seller=SELLER, buyer=_party(order.bill_to or st), ship_to=_party(st))


async def po_pdf(s, order: Order) -> bytes:
    lines = (await s.execute(select(OrderLine).where(OrderLine.order_id == order.id, OrderLine.removed.is_(False))
                             .order_by(OrderLine.line_no))).scalars()
    return render(PdfDoc(title="Purchase order", number=order.source_order_number or str(order.order_number),
                         when=order.order_date, lines=[PdfLine(ln.item_code, (ln.source or {}).get("buyer_part"),
                                                               f"{ln.ordered_qty.normalize():f}", ln.order_unit or "EA",
                                                               ln.unit_price, None) for ln in lines], **_common(order)))


async def invoice_pdf(s, inv: Invoice, order: Order) -> bytes:
    lines = (await s.execute(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id).order_by(InvoiceLine.line_no))).scalars()
    return render(PdfDoc(title="Invoice", number=inv.invoice_number, when=inv.invoice_date, terms=inv.payment_terms,
                         currency=inv.currency, total=inv.total,
                         lines=[PdfLine(ln.item_code, ln.description, ln.qty, ln.uom, ln.unit_price, ln.amount) for ln in lines],
                         **_common(order, inv.delivery_number)))


async def shipped_pdf(s, order: Order, when: date | None, delivery_number: str | None = None) -> bytes:
    """What every applied 945 of the order shipped, row by row, with lot and pallet."""
    rows = (await s.execute(select(ShipmentRow).join(Shipment, Shipment.id == ShipmentRow.shipment_id).where(
        Shipment.order_id == order.id, Shipment.applied.is_(True)).order_by(ShipmentRow.lx_index, ShipmentRow.w12_index))).scalars()
    return render(PdfDoc(title="Packing list", number=str(order.order_number), when=when, priced=False,
                         lines=[PdfLine(r.item_code, None, r.qty_shipped_ea, "EA", extra=" / ".join(x for x in (r.lot, r.sscc) if x))
                                for r in rows], **_common(order, delivery_number)))


async def transfer_pdf(s, t: Transfer) -> bytes:
    """The transfer order: from and to, and what moves, in EA."""
    frm, to = await s.get(Warehouse, t.from_warehouse_id), await s.get(Warehouse, t.to_warehouse_id)
    lines = (await s.execute(select(TransferLine).where(TransferLine.transfer_id == t.id).order_by(TransferLine.line_no))).scalars()
    def wh(w: Warehouse | None) -> Party:
        return _party({"name": w.name, "address1": w.address1, "city": w.city, "province": w.province, "zip": w.zip}) if w else Party()
    return render(PdfDoc(title="Transfer order", number=t.transfer_number, when=t.created_at.date() if t.created_at else None,
                         po=None, order_number=0, order_ref=f"TO-{t.transfer_number}", seller=wh(frm), buyer=wh(frm),
                         ship_to=wh(to), priced=False,
                         lines=[PdfLine(ln.item_code, None, ln.qty_ea, "EA") for ln in lines]))


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "file"


async def packet(s, order: Order) -> bytes:
    """Everything kept for the order in one zip: its stored files as received or sent, and the PDFs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        docs = (await s.execute(select(Document).join(DocumentLink, DocumentLink.document_id == Document.id).where(
            DocumentLink.entity_type == "order", DocumentLink.entity_id == order.id).order_by(Document.id))).scalars().all()
        for d in docs:
            content = (await s.execute(select(Document.content).where(Document.id == d.id))).scalar_one()
            if content is not None:
                z.writestr(f"files/{d.id}-{_safe(d.remote_name or d.kind)}", bytes(content))
        z.writestr(f"PO-{_safe(order.source_order_number or str(order.order_number))}.pdf", await po_pdf(s, order))
        inv = (await s.execute(select(Invoice).where(Invoice.order_id == order.id))).scalar_one_or_none()
        if inv is not None:
            z.writestr(f"{_safe(inv.invoice_number)}.pdf", await invoice_pdf(s, inv, order))
        if (await s.execute(select(Shipment.id).where(Shipment.order_id == order.id, Shipment.applied.is_(True)).limit(1))).first():
            z.writestr(f"PackingList-{order.order_number}.pdf", await shipped_pdf(s, order, None))
    return buf.getvalue()
