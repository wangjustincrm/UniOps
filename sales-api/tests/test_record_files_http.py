"""The files a list declares, over HTTP (stage 4): the name, the type and how it is shown are the declaration's; a person
without the permission gets nothing, one who holds it the file; a record that is not there is said."""
import pytest

from app.services.invoice_views import InvoiceService
from app.services.operations import DocumentService
from app.services.order_views import OrderService
from app.services.settlement_list import SettlementRowService
from app.services.transfer_views import TransferService
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_orders_api import NOBODY, READER, api  # noqa: F401 — api is a fixture

NAME = 'PO-Käse "8"/1.pdf'  # what a customer's PO number can hold
CASES = [(OrderService, "po_pdf", "/orders/7/po.pdf", "application/pdf", "inline"), (OrderService, "packet", "/orders/7/packet.zip", "application/zip", "attachment"),
         (InvoiceService, "pdf", "/invoices/7/invoice.pdf", "application/pdf", "inline"), (TransferService, "pdf", "/transfers/7/print.pdf", "application/pdf", "inline"),
         (SettlementRowService, "statement_pdf", "/settlements/7/statement.pdf", "application/pdf", "inline"),
         (SettlementRowService, "report_pdf", "/settlements/report/settlements.pdf?sort=-settled_date", "application/pdf", "inline"),
         (DocumentService, "raw", "/documents/7/raw", "application/octet-stream", "attachment")]


@pytest.mark.parametrize(("svc", "method", "path", "media", "shown"), CASES, ids=[c[2] for c in CASES])
async def test_a_file_is_answered_as_declared_and_needs_the_permission(api, monkeypatch, svc, method, path, media, shown):  # noqa: F811
    assert (await api.get("/api/v1" + path, headers=NOBODY)).status_code == 403
    seen = []

    async def made(self, arg):
        seen.append(arg)
        return NAME, b"the bytes"
    monkeypatch.setattr(svc, method, made)
    r = await api.get("/api/v1" + path, headers=READER)
    assert (r.status_code, r.content, r.headers["content-type"]) == (200, b"the bytes", media)
    head = r.headers["content-disposition"]
    assert head.startswith(f'{shown}; filename="PO-Kase _8__1.pdf"; filename*=UTF-8\'\'PO-K%C3%A4se')
    assert seen == [7] or seen[0].sort  # the record's id, or the list's query


@pytest.mark.parametrize("path", ["/orders/99999/po.pdf", "/orders/99999/packet.zip", "/invoices/99999/invoice.pdf", "/transfers/99999/print.pdf",
                                  "/settlements/99999/statement.pdf", "/documents/99999/raw"])
async def test_one_that_is_not_there_is_said(api, path):  # noqa: F811
    r = await api.get("/api/v1" + path, headers=READER)
    assert (r.status_code, r.json()["error"]["code"]) == (404, "entity.not_found")


async def test_meta_says_the_files(api):  # noqa: F811
    meta = (await api.get("/api/v1/orders/meta", headers=READER)).json()
    # `allowed`: whether this person may have it (a file with a key of its own, e.g. a recall; else reading is enough).
    assert meta["capabilities"]["files"] == [{"path": "po.pdf", "media_type": "application/pdf", "over": "record", "allowed": True},
                                             {"path": "packet.zip", "media_type": "application/zip", "over": "record", "allowed": True}]
    assert (await api.get("/api/v1/lots/meta", headers=READER)).json()["capabilities"]["files"] == []
