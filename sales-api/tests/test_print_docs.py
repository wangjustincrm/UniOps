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
