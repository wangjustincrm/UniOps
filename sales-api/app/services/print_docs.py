"""The documents a person prints or downloads (FR-OB-55): the purchase order, the invoice, the
packing list (of everything shipped, or of one shipment), the transfer order, and an order's audit
packet (every stored file of the order with these PDFs, in one zip). The same PDFs go with an
emailed invoice. Their look is pdf_layout's (2026-11-12, after the user's templates)."""
from __future__ import annotations

import collections
import io
import re
import zipfile
from dataclasses import replace
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.domain.carriers import tracking_url
from app.integrations.branding import company_logo
from app.models.config import Item, Partner, Warehouse
from app.models.documents import Document, DocumentLink
from app.models.invoices import Invoice, InvoiceLine
from app.models.orders import Order, OrderLine
from app.models.shipments import Shipment, ShipmentRow
from app.models.transfers import Receipt, ReceiptRow, Transfer, TransferLine
from app.services import pdf_layout as L
from app.services.carriers import carriers_by_scac
from app.services.pdf_layout import Card, Col, Company

# The seller on every document (the user's templates). The logo comes from the company branding.
COMPANY = Company("Canada Royal Milk ULC", ["1680 Venture Drive", "Kingston, ON K7P 0E9", "Canada"],
                  email="ar@canadaroyalmilk.com", phone="+1 613-817-1228")
SELLER = COMPANY


async def _company() -> Company:
    return replace(COMPANY, logo=await company_logo())


def _address(label: str, a: dict | None) -> Card:
    a = a or {}
    place = ", ".join(x for x in (a.get("city"), " ".join(filter(None, (a.get("province"), a.get("zip")))), a.get("country")) if x)
    return Card(label, a.get("name"), [x for x in (a.get("address1"), a.get("address2"), place) if x],
                [f"Location: {a['gln']}"] if a.get("gln") else [])


async def _partner(s, order: Order) -> str | None:
    return order.partner_name or (await s.execute(select(Partner.name).where(Partner.code == order.partner_code))).scalar()


async def _names(s, codes) -> dict[str, str | None]:
    codes = {c for c in codes if c}
    if not codes:
        return {}
    return dict((await s.execute(select(Item.item_code, Item.description).where(Item.item_code.in_(codes)))).all())


def _described(description: str | None, src: dict | None) -> tuple[str, str] | str:
    """The line's words, and under them what the retailer reads it by: buyer part, UPC, GTIN, pack."""
    src = src or {}
    extra = " · ".join(x for x in (
        f"Buyer part {src['buyer_part']}" if src.get("buyer_part") else None, f"UPC {src['upc']}" if src.get("upc") else None,
        f"GTIN {src['gtin']}" if src.get("gtin") else None, f"Pack {src['inner_packs']}" if src.get("inner_packs") else None) if x)
    return (description or "", extra) if extra else (description or "")


def _po_number(order: Order) -> str:
    return order.source_order_number or str(order.order_number)


async def _order_lines(s, order: Order) -> list[OrderLine]:
    return list((await s.execute(select(OrderLine).where(OrderLine.order_id == order.id, OrderLine.removed.is_(False))
                                 .order_by(OrderLine.line_no))).scalars())


# ── Purchase order ──────────────────────────────────────────────────────────

async def po_pdf(s, order: Order) -> bytes:
    """The customer's order as received: buyer, ship-to, us as the vendor, the lines at the PO price."""
    company, t = await _company(), L.PO
    lines = await _order_lines(s, order)
    names = await _names(s, [ln.item_code for ln in lines])
    po = _po_number(order)
    bill = _address("Bill to", order.bill_to or order.ship_to)
    rows = [[ln.item_code or (ln.source or {}).get("buyer_part") or "",
             _described((ln.source or {}).get("description") or names.get(ln.item_code or ""), ln.source),
             L.qty(ln.ordered_qty), ln.order_unit or "EA", L.money(ln.unit_price),
             L.money(ln.unit_price * ln.ordered_qty if ln.unit_price is not None else None)] for ln in lines]
    lines_total = sum((ln.unit_price * ln.ordered_qty for ln in lines if ln.unit_price is not None), Decimal(0))
    box = [("Subtotal", L.money(order.subtotal if order.subtotal is not None else lines_total))]
    box += [(k, L.money(v)) for k, v in (("Shipping", order.shipping), ("Discount", -order.discount if order.discount else None),
                                          ("Tax", order.tax)) if v]
    box.append(("PO total", f"{L.money(order.total if order.total is not None else lines_total)} {order.currency or 'CAD'}"))
    story = [
        L.header(company, t, "Purchase order", f"PO-{po}", [
            ("PO date", L.day(order.order_date)), ("Requested delivery", L.day(order.requested_delivery_date)),
            ("Do not deliver after", L.day(order.do_not_deliver_after)), ("Order", order.source_reference),
            ("Internal no.", str(order.order_number))],
            party=Card("Buyer / Bill to", bill.name, bill.lines, [x.replace("Location", "Billing location") for x in bill.small])),
        L.gap(4), L.section("Purchase Order Details", "Buyer, delivery and vendor information", t), L.gap(2.5),
        L.cards([bill, _address("Ship to", order.ship_to),
                 Card("Vendor / Supplier", company.name, company.address, [x for x in (company.email, company.phone) if x])], t),
        L.gap(5), L.section("Purchase Order Items", "Ordered quantity and pricing", t), L.gap(2.5),
        L.items([Col("Item", 1.2), Col("Description", 3.6), Col("Qty", 0.8, "RIGHT"), Col("UOM", 0.7, "CENTER"),
                 Col("Unit price", 1.0, "RIGHT"), Col("Amount", 1.15, "RIGHT", strong=True)], rows, t),
        L.gap(5),
        L.closing("Purchase Order Notes", [f"Please reference PO-{po} on all documents.", *([order.notes] if order.notes else [])], box, t),
    ]
    return L.build(story, company=company, title=f"Purchase order PO-{po}", theme=t)


# ── Invoice ─────────────────────────────────────────────────────────────────

async def invoice_pdf(s, inv: Invoice, order: Order) -> bytes:
    """What is billed: the delivered quantity (shipped − returned) at the invoice price."""
    company, t = await _company(), L.INVOICE
    lines = list((await s.execute(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id).order_by(InvoiceLine.line_no))).scalars())
    by_id = {ln.id: ln for ln in await _order_lines(s, order)}
    partner = await _partner(s, order)
    currency = inv.currency or "CAD"
    rows = []
    for ln in lines:
        ol = by_id.get(ln.order_line_id) if ln.order_line_id else None
        ordered = f"{L.qty(ol.ordered_qty)} {ol.order_unit or ''}".strip() if ol else ""
        rows.append([ln.item_code or "", _described(ln.description, {"buyer_part": ln.buyer_part, **((ol.source or {}) if ol else {})}),
                     ordered, L.qty(ln.qty), ln.uom or "", L.money(ln.unit_price), L.money(ln.amount)])
    returned = [f"{ol.item_code}: {L.qty(ol.returned_ea)} EA" for ol in by_id.values() if ol.returned_ea]
    box = [("Subtotal", L.money(inv.subtotal))]
    if inv.total is not None and inv.subtotal is not None and inv.total != inv.subtotal:
        box.append(("Tax", L.money(inv.total - inv.subtotal)))
    box.append(("Balance due", f"{L.money(inv.total)} {currency}"))
    notes = [f"Payment terms: {inv.payment_terms or 'per customer agreement'}.",
             f"Please reference invoice {inv.invoice_number} with payment."]
    if returned:
        notes.insert(0, f"Returned by the customer, not billed: {', '.join(returned)}.")
    if company.email:
        notes.append(f"Questions: {company.email}")
    story = [
        L.header(company, t, "Invoice", inv.invoice_number, [
            ("Balance due", f"{L.money(inv.total)} {currency}"), ("Invoice date", L.day(inv.invoice_date))]),
        L.gap(4), L.section("Customer & Invoice Details", "Billing, shipping and invoice information", t), L.gap(2.5),
        L.cards([_address("Bill to", order.bill_to or order.ship_to), _address("Ship to", order.ship_to),
                 Card("Invoice details", partner, facts=[
                     ("Invoice date", L.day(inv.invoice_date)), ("Customer PO", order.source_order_number),
                     ("Order", f"{order.ref} (internal no. {order.order_number})"), ("Delivery no.", inv.delivery_number)])], t),
        L.gap(5), L.section("Invoice Items", "Delivered quantity and pricing", t), L.gap(2.5),
        L.items([Col("Item", 1.1), Col("Description", 3.1), Col("Ordered", 1.0, "RIGHT"), Col("Invoiced", 0.85, "RIGHT"),
                 Col("UOM", 0.6, "CENTER"), Col("Unit price", 1.0, "RIGHT"), Col("Amount", 1.15, "RIGHT", strong=True)], rows, t),
        L.gap(5),
        L.closing("Payment & Notes", notes, box, t, lead="Thank you for your business."),
    ]
    return L.build(story, company=company, title=f"Invoice {inv.invoice_number}", theme=t)


# ── Packing list ────────────────────────────────────────────────────────────

async def _packing(s, order: Order, ships: list[Shipment], when: date | None, delivery_number: str | None,
                   warning: str | None = None) -> bytes:
    """The order's shipped goods: per item ordered vs shipped, then per pallet and lot."""
    company, t = await _company(), L.PACKING
    ids = [x.id for x in ships]
    rows = list((await s.execute(select(ShipmentRow).where(ShipmentRow.shipment_id.in_(ids))
                                 .order_by(ShipmentRow.shipment_id, ShipmentRow.lx_index, ShipmentRow.w12_index))).scalars()) if ids else []
    lines = await _order_lines(s, order)
    names = await _names(s, [r.item_code for r in rows] + [ln.item_code for ln in lines])
    partner = await _partner(s, order)
    carriers = await carriers_by_scac(s, [x.carrier for x in ships])
    tracking = list(dict.fromkeys(n for x in ships for n in (x.tracking or [])))
    carrier_names = list(dict.fromkeys(
        (f"{c.name} ({x.carrier})" if (c := carriers.get((x.carrier or "").strip().upper())) else x.carrier) for x in ships if x.carrier))
    ship_date = when or max((x.ship_date for x in ships if x.ship_date), default=None)
    number = f"PL-{order.order_number}" + (f"-{ships[0].id}" if len(ships) == 1 and warning else "")

    shipped_by_line: dict[int | str, int] = collections.Counter()
    for r in rows:
        shipped_by_line[r.order_line_id or r.item_code or ""] += r.qty_shipped_ea
    summary = []
    for ln in lines:
        shipped = shipped_by_line.pop(ln.id, 0) + shipped_by_line.pop(ln.item_code or "", 0)
        summary.append((ln.item_code or "", names.get(ln.item_code or "") or (ln.source or {}).get("description") or "",
                        f"{L.qty(ln.ordered_qty)} {ln.order_unit or 'EA'}", ln.qty_ea, shipped))
    summary += [(str(k), names.get(str(k)) or "", "", 0, v) for k, v in shipped_by_line.items() if v]  # shipped, not on the order
    short = any(sh < ea for _, _, _, ea, sh in summary)
    s_cols = [Col("Item", 1.1), Col("Description", 2.8), Col("Ordered", 1.1, "RIGHT"), Col("Ordered EA", 0.9, "RIGHT"),
              Col("Shipped EA", 0.9, "RIGHT", strong=True)] + ([Col("Short EA", 0.8, "RIGHT")] if short else [])
    s_rows = [[c, d, o, L.qty(ea), L.qty(sh)] + ([L.qty(max(ea - sh, 0)) if ea > sh else ""] if short else [])
              for c, d, o, ea, sh in summary]
    s_foot = ["Total", "", "", L.qty(sum(x[3] for x in summary)), L.qty(sum(x[4] for x in summary))] + (
        [L.qty(sum(max(x[3] - x[4], 0) for x in summary))] if short else [])

    template_of = {x.id: (c.tracking_url if (c := carriers.get((x.carrier or "").strip().upper())) else None) for x in ships}
    pallets: dict[tuple[int, int], list[ShipmentRow]] = {}
    for r in rows:
        pallets.setdefault((r.shipment_id, r.lx_index), []).append(r)
    p_rows: list[list] = []
    subtotals: list[int] = []
    for prs in pallets.values():
        for i, r in enumerate(prs):
            url = tracking_url(template_of.get(r.shipment_id), r.tracking)
            p_rows.append([(r.sscc or "", f"Pallet {r.lx_number or r.lx_index}") if i == 0 else "",
                           (r.item_code or "", names.get(r.item_code or "") or ""), r.lot or "", L.day(r.expiry),
                           L.qty(r.qty_shipped_ea), (r.tracking or "", url) if url else (r.tracking or "")])
        if len(pallets) > 1 and len(prs) > 1:  # a pallet of one row is its own total
            p_rows.append(["", "", "Pallet total", "", L.qty(sum(r.qty_shipped_ea for r in prs)), ""])
            subtotals.append(len(p_rows) - 1)
    p_cols = [Col("Pallet (SSCC)", 1.9), Col("Item", 2.0), Col("Lot", 1.4), Col("Expiry", 0.95, "CENTER"), Col("Qty EA", 0.75, "RIGHT"),
              Col("Tracking", 1.6)]
    p_foot: list = ["Shipment total", "", "", "", L.qty(sum(r.qty_shipped_ea for r in rows)), ""]
    if not any(r.tracking for r in rows):  # no tracking (a pallet shipment): the column goes
        p_cols, p_foot, p_rows = p_cols[:-1], p_foot[:-1], [x[:-1] for x in p_rows]
    notes = ["Please verify all items upon receipt.", f"Please reference packing list {number}."]
    if len(ships) == 1:
        notes.append(f"From the warehouse's 945 #{ships[0].document_id}"
                     + (f" (940 ref. {ships[0].our_reference})." if ships[0].our_reference else "."))
    if company.email:
        notes.append(f"Questions: {company.email}")
    story = [
        L.header(company, t, "Packing list", number, [("Ship date", L.day(ship_date)), ("Customer PO", order.source_order_number)]),
    ]
    if warning:
        story += [L.gap(3), L.note(warning, t)]
    story += [
        L.gap(4), L.section("Customer & Shipping Details", "Billing, delivery and packing information", t), L.gap(2.5),
        L.cards([_address("Bill to", order.bill_to or order.ship_to), _address("Ship to", (ships[0].ship_to if len(ships) == 1 and ships[0].ship_to else None) or order.ship_to),
                 Card("Shipment details", partner, facts=[
                     ("Order", f"{order.ref} (internal no. {order.order_number})"), ("Carrier", ", ".join(carrier_names) or None), ("BOL", ", ".join(x.bol for x in ships if x.bol) or None),
                     ("Tracking", ", ".join(tracking) or None), ("Delivery no.", delivery_number)])], t),
        L.gap(5), L.section("Order & Shipment Summary", "Ordered and shipped, per item", t), L.gap(2.5),
        L.items(s_cols, s_rows, t, foot=s_foot),
        L.gap(5), L.section("Packing & Lot Details", "Shipped quantities by pallet, item and lot", t), L.gap(2.5),
        L.items(p_cols, p_rows, t, subtotals=subtotals, foot=p_foot) if p_rows else L.p("Nothing shipped yet.", 8.5, color=L.MUTED),
        L.gap(5),
        L.closing("Shipping Notes", notes, [("Customer PO", order.source_order_number or "—"), ("Pallets", str(len(pallets))),
                                            ("Packing list", number)], t),
    ]
    return L.build(story, company=company, title=f"Packing list {number}", theme=t)


async def shipped_pdf(s, order: Order, when: date | None, delivery_number: str | None = None) -> bytes:
    """Everything the order's applied 945s shipped (the invoice email's packing list)."""
    ships = list((await s.execute(select(Shipment).where(Shipment.order_id == order.id, Shipment.applied.is_(True))
                                  .order_by(Shipment.id))).scalars())
    return await _packing(s, order, ships, when, delivery_number)


async def shipment_pdf(s, order: Order, sh: Shipment) -> bytes:
    """One shipment (a 945 set) as packed. Any 945 kept: a second one, not applied, says so."""
    warning = None if sh.applied or sh.shadow else "A second 945 for this order: kept, not applied."
    return await _packing(s, order, [sh], sh.ship_date, None, warning)


# ── Transfer order ──────────────────────────────────────────────────────────

async def transfer_pdf(s, t: Transfer) -> bytes:
    """The transfer order: from and to, and what moves, in EA."""
    company, th = await _company(), L.TRANSFER
    frm, to = await s.get(Warehouse, t.from_warehouse_id), await s.get(Warehouse, t.to_warehouse_id)
    lines = list((await s.execute(select(TransferLine).where(TransferLine.transfer_id == t.id).order_by(TransferLine.line_no))).scalars())
    # What the applied 944 brought that is on no line (a different item came: a discrepancy), summed per item.
    extra: dict[str, int] = {}
    for code, q in (await s.execute(select(ReceiptRow.item_code, ReceiptRow.qty_ea).join(Receipt, Receipt.id == ReceiptRow.receipt_id).where(
            Receipt.transfer_id == t.id, Receipt.applied.is_(True), ReceiptRow.transfer_line_no.is_(None)))).all():
        extra[code or "?"] = extra.get(code or "?", 0) + int(q or 0)
    received_any = any(ln.received_ea for ln in lines) or bool(extra)
    names = await _names(s, [ln.item_code for ln in lines] + list(extra))
    rows = [[ln.item_code, names.get(ln.item_code) or "", L.qty(ln.qty_ea), L.qty(ln.received_ea) if received_any else ""] for ln in lines]
    rows += [[code, (names.get(code) or "", "received, not on the transfer"), "", L.qty(q)] for code, q in extra.items()]
    received_total = sum(ln.received_ea for ln in lines) + sum(extra.values())

    def wh(label: str, w: Warehouse | None) -> Card:
        return _address(label, {"name": w.name, "address1": w.address1, "city": w.city, "province": w.province, "zip": w.zip,
                                "country": w.country}) if w else Card(label)
    created = t.created_at.date() if t.created_at else None
    story = [
        L.header(company, th, "Transfer order", f"TO-{t.transfer_number}", [("Created", L.day(created)), ("Status", t.status)]),
        L.gap(4), L.section("Transfer Details", "Where the goods leave from and go to", th), L.gap(2.5),
        L.cards([wh("From", frm), wh("To", to), Card("Transfer details", f"TO-{t.transfer_number}", facts=[
            ("Created", L.day(created)), ("Status", t.status), ("Notes", t.notes)])], th),
        L.gap(5), L.section("Transfer Items", "Quantities to move, in each", th), L.gap(2.5),
        L.items([Col("Item", 1.3), Col("Description", 4.0), Col("Qty EA", 1.0, "RIGHT", strong=True), Col("Received EA", 1.0, "RIGHT")],
                rows, th, foot=["Total", "", L.qty(sum(ln.qty_ea for ln in lines)), L.qty(received_total) if received_any else ""]),
        L.gap(5),
        L.closing("Notes", ["Please verify all items upon receipt.", f"Please reference transfer order TO-{t.transfer_number}."],
                  [("Lines", str(len(lines))), ("Total EA", L.qty(sum(ln.qty_ea for ln in lines)))], th),
    ]
    return L.build(story, company=company, title=f"Transfer order TO-{t.transfer_number}", theme=th)


# ── Audit packet ────────────────────────────────────────────────────────────

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
        z.writestr(f"PO-{_safe(_po_number(order))}.pdf", await po_pdf(s, order))
        inv = (await s.execute(select(Invoice).where(Invoice.order_id == order.id))).scalar_one_or_none()
        if inv is not None:
            z.writestr(f"{_safe(inv.invoice_number)}.pdf", await invoice_pdf(s, inv, order))
        if (await s.execute(select(Shipment.id).where(Shipment.order_id == order.id, Shipment.applied.is_(True)).limit(1))).first():
            z.writestr(f"PackingList-{order.order_number}.pdf", await shipped_pdf(s, order, None))
    return buf.getvalue()
