"""The documents a person downloads (FR-OB-55): PO, invoice, transfer order, the order's audit packet."""
import io
import zipfile
from datetime import date

from app.services.invoice_views import InvoiceService
from app.services.order_views import OrderService
from tests.test_invoicing import confirm, p4_edi, p6, shipped_850  # noqa: F401 — fixtures
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_transfers import create, p7  # noqa: F401 — a fixture


async def test_the_po_the_invoice_and_the_packet_of_an_order(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-P")
    await p6.inv.advance()
    inv = await confirm(p6, o.id, when=date(2026, 11, 5))
    async with p6.factory(OP) as u:
        name, po = await OrderService(u).po_pdf(o.id)
        iname, ipdf = await InvoiceService(u).pdf(inv.id)
        zname, data = await OrderService(u).packet(o.id)
    assert name == "PO-PO-P.pdf" and po.startswith(b"%PDF")
    assert iname == f"{inv.invoice_number}.pdf" and ipdf.startswith(b"%PDF")
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert zname.endswith(".zip") and "PO-PO-P.pdf" in names and f"{inv.invoice_number}.pdf" in names
    assert f"PackingList-{o.order_number}.pdf" in names
    assert any(n.startswith("files/") and "945" in n for n in names) and any(n.startswith("files/") for n in names)


async def test_the_transfer_order_prints(p7):  # noqa: F811
    from app.services.transfer_views import TransferService
    x = await create(p7)
    async with p7.factory(OP) as u:
        name, pdf = await TransferService(u).pdf(x.id)
    assert name == f"TO-{x.transfer_number}.pdf" and pdf.startswith(b"%PDF")


async def test_the_documents_read_as_the_templates_do_and_fit_the_page(p6):  # noqa: F811
    """2026-11-12, after the user's templates: the seller with its address, the cards, words for
    headings, amounts with separators, the delivered quantity billed, a page count on every page."""
    from tests.test_order_ref import pdf_text
    o = await shipped_850(p6, "PO-T")
    await p6.inv.advance()
    inv = await confirm(p6, o.id, when=date(2026, 11, 5))
    async with p6.factory(OP) as u:
        _, po = await OrderService(u).po_pdf(o.id)
        _, ipdf = await InvoiceService(u).pdf(inv.id)
    po_text, inv_text = pdf_text(po), pdf_text(ipdf)
    for s in (b"PURCHASE ORDER", b"PO-PO-T", b"Buyer / Bill to".upper(), b"VENDOR / SUPPLIER", b"Canada Royal Milk ULC",
              b"1680 Venture Drive", b"Unit price", b"PO TOTAL", b"Please reference PO-PO-T on all documents.", b"Page 1 of 1"):
        assert s in po_text, s
    for s in (b"INVOICE", inv.invoice_number.encode(), b"BALANCE DUE", b"INVOICE DETAILS", b"Invoiced", b"05-Nov-2026",
              f"Please reference invoice {inv.invoice_number} with payment.".encode(), b"Thank you for your business.", b"Page 1 of 1"):
        assert s in inv_text, s
    assert b"Quantity_Ordered" not in po_text + inv_text  # words, not field names


def test_a_long_order_breaks_over_pages_and_counts_them():
    from app.services import pdf_layout as L
    from tests.test_order_ref import pdf_text
    company = L.Company("Seller", ["1 Road"], logo=b"not an image")  # a bad logo prints without it
    rows = [[f"ITEM{i}", ("Description", "UPC 1"), "1", "EA", "1.00", "1,000.00"] for i in range(120)]
    pdf = L.build([L.header(company, L.PO, "Purchase order", "PO-1"),
                   L.items([L.Col("Item", 1), L.Col("Description", 3), L.Col("Qty", 1, "RIGHT"), L.Col("UOM", 1),
                            L.Col("Unit price", 1, "RIGHT"), L.Col("Amount", 1, "RIGHT", strong=True)], rows, L.PO)],
                  company=company, title="Purchase order PO-1", theme=L.PO)
    body = pdf_text(pdf)
    n = body.count(b"Page ")
    assert n >= 3 and f"Page {n} of {n}".encode() in body and b"ITEM119" in body
    assert L.money(__import__("decimal").Decimal("42182.4")) == "42,182.40" and L.qty(__import__("decimal").Decimal("1200.000")) == "1,200"
