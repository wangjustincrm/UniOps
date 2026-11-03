"""Where a Sales task opens (design §7: "Task Inbox links open the right
Order Bridge page"). The task's problem key `SALES:<event>:<entity>` is its
`document_number`; epms and the Portal link every Sales task to the Sales page
`/inbox/<key>`, which asks here for the page and the record to open. One map,
here, for every event — a new event that is not listed opens the home page."""
from __future__ import annotations

from sqlalchemy import select

from app.models.invoices import Invoice
from app.models.records import OutboxRecord
from app.models.shipments import Shipment
from app.models.transfers import Receipt
from app.platform.authorization import requires
from app.platform.errors import InvalidInput
from app.schemas.health import InboxTarget
from app.services.problems import PREFIX

READ = "sales.read"

ORDERS = ("/orders", "/orders")
DOCUMENTS = ("/documents", "/documents")
CONNECTIONS = ("/config/connections", "/config/connections")
TRANSFERS = ("/transfers", "/transfers")
INVOICES = ("/invoices", "/invoices")
RULES = ("/config/notifications", "/config/notification-rules")

BY_ORDER = {"order_review", "order_archived", "changed_after_940", "940_failed", "940_rejected", "940_timeout",
            "short_shipment", "second_945", "invoice_hold", "order_return", "cancel_after_940"}
BY_DOCUMENT = {"unreadable_file", "order_file_bad", "997_unmatched"}
BY_CONNECTION = {"sftp_fetch_failed", "sftp_auth_failed", "file_not_stored", "shopify_fetch_failed",
                 "shopify_auth_failed"}
BY_SHIPMENT = {"945_no_order", "945_line", "shopify_write", "945_validation"}
BY_TRANSFER = {"transfer_failed", "943_rejected", "943_timeout", "transfer_discrepancy", "partial_receipt", "second_944"}

# The tab of the record's sheet a task is about (the Sales UI's RecordSheet tab ids).
ORDER_TAB = {"order_review": "overview", "order_archived": "overview", "cancel_after_940": "overview",
             "changed_after_940": "documents", "940_failed": "documents", "940_rejected": "documents", "940_timeout": "documents",
             "short_shipment": "shipments", "second_945": "shipments", "945_line": "shipments", "945_validation": "shipments",
             "shopify_write": "shipments", "invoice_hold": "invoice", "order_return": "invoice", "invoice_failed": "invoice"}
CONNECTION_TAB = {"sftp_fetch_failed": "schedule", "shopify_fetch_failed": "schedule", "file_not_stored": "files",
                  "sftp_auth_failed": "overview", "shopify_auth_failed": "overview"}
TRANSFER_TAB = {"transfer_discrepancy": "receipts", "partial_receipt": "receipts", "second_944": "receipts"}


def _target(where: tuple[str, str], id: int | None, tab: str | None = None) -> InboxTarget:
    return InboxTarget(page=where[0], resource=where[1], id=id, tab=tab)


class InboxService:
    def __init__(self, uow) -> None:
        self.uow = uow
        self.s = uow.session

    @requires(READ)
    async def resolve(self, key: str) -> InboxTarget:
        prefix, _, rest = key.partition(":")
        event, _, entity = rest.rpartition(":")
        if prefix != PREFIX or not event or not entity.isdigit():
            raise InvalidInput("inbox.bad_key", f"not a Sales task key: {key!r}")
        return await self._for(event, int(entity))

    async def _for(self, event: str, n: int) -> InboxTarget:
        s = self.s
        if event in BY_ORDER:
            return _target(ORDERS, n, ORDER_TAB.get(event))
        if event == "invoice_failed":  # entity: the order; the invoices list opens the invoice
            inv = (await s.execute(select(Invoice.id).where(Invoice.order_id == n))).scalar_one_or_none()
            return _target(INVOICES, inv) if inv else _target(ORDERS, n, ORDER_TAB[event])
        if event in BY_DOCUMENT:
            return _target(DOCUMENTS, n)
        if event in BY_CONNECTION:
            return _target(CONNECTIONS, n, CONNECTION_TAB.get(event))
        if event in BY_TRANSFER:
            return _target(TRANSFERS, n, TRANSFER_TAB.get(event))
        if event in BY_SHIPMENT:
            sh = await s.get(Shipment, n)
            if sh is None:
                return _target(DOCUMENTS, None)
            return _target(ORDERS, sh.order_id, ORDER_TAB.get(event)) if sh.order_id else _target(DOCUMENTS, sh.document_id)
        if event == "944_no_transfer":
            r = await s.get(Receipt, n)
            return _target(DOCUMENTS, r.document_id if r else None)
        if event == "send_refused":
            row = await s.get(OutboxRecord, n)
            etype, eid = (row.entity_type, row.entity_id) if row else (None, None)
            if etype == "sales_orders":
                return _target(ORDERS, int(eid), "documents")
            if etype == "sales_transfers":
                return _target(TRANSFERS, int(eid))
            if etype == "sales_invoices":
                return _target(INVOICES, int(eid))
            if etype == "sales_shipments":
                return await self._for("shopify_write", int(eid))
            return InboxTarget(page="/runs", resource=None, id=None)
        if event == "test":
            return _target(RULES, n)
        if event == "partner_no_abbrev":
            return _target(("/config/partners", "/config/partners"), n)
        return InboxTarget(page="/home", resource=None, id=None)
