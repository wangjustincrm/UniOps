"""The document register, runs and log (FR-OB-53): read with sales.read.
"Run now" (design §4.1) claims a run through the scheduler's own single-flight
path and needs sales.file.retry."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select, union_all

from app.domain.documents import (
    DIRECTIONS,
    KINDS,
    LOG_LEVELS,
    ORIGINS,
    RUN_STATUSES,
    RUN_TRIGGERS,
    SYSTEMS,
)
from app.domain.intake import classify
from app.domain.status import INBOUND_FILE, Ack, InboundFile, OutboundFile
from app.models.documents import Document, DocumentLink, LogEntry, Run
from app.models.invoices import Invoice
from app.models.orders import Order
from app.models.shipments import (
    Shipment,  # noqa: F401 — maps Document.shipments_without_order
)
from app.models.transfers import Receipt, Transfer
from app.platform.authorization import authorize, requires
from app.platform.commands import ANY_VERSION
from app.platform.errors import BusinessError, NotFound
from app.platform.query import FilterRule
from app.platform.repository import Repository
from app.platform.services import ReadOnlyService
from app.platform.views import Preset
from app.schemas.operations import DocumentRead, EntityRef, LogRead, RunRead
from app.services.problems import ORDER_FILE_UNREADABLE, UNREADABLE_FILE

READ = "sales.read"
FILE_RETRY = "sales.file.retry"


class DocumentRepository(Repository[Document]):
    model = Document
    search_fields = ("remote_name", "sha256", "error", "order_refs")
    list_fields = ("id", "direction", "origin", "system", "connection_id", "kind", "remote_name", "size",
                   "remote_mtime", "sha256", "processing_status", "file_status", "ack_status", "isa13", "gs06",
                   "st02", "partner_code", "error", "sent_at", "created_at")
    labels = {"remote_name": "File", "remote_mtime": "Modified on the server", "isa13": "ISA13", "gs06": "GS06",
              "st02": "ST02", "sha256": "SHA-256", "order_refs": "Order"}
    enums = {"direction": DIRECTIONS, "origin": ORIGINS, "system": SYSTEMS, "kind": KINDS,
             "processing_status": tuple(s.value for s in InboundFile),
             "file_status": tuple(s.value for s in OutboundFile), "ack_status": tuple(s.value for s in Ack)}
    filterable = {"shipments_without_order": FilterRule(Document.shipments_without_order),
                  # an order's ref, internal or source number (user 2026-11-09): its documents
                  "order_refs": FilterRule(Document.order_refs, kind="text", cast=str)}
    meta_hidden = ("shipments_without_order",)
    default_sort = ("-created_at",)
    groupable = ("direction", "origin", "system", "kind", "processing_status", "file_status", "ack_status",
                 "partner_code", "connection_id")
    presets = (
        Preset("all", "All"),
        Preset("inbound_failed", "Inbound failed", {"filters": [{"field": "direction", "op": "eq", "value": "IN"}, {"field": "processing_status", "op": "eq", "value": "Failed"}]}),
        Preset("duplicates_not_ours", "Duplicates / not ours",
               {"filters": [{"field": "processing_status", "op": "in", "value": ["Duplicate", "Skipped"]}]}),
        Preset("outbound", "Outbound", {"filters": [{"field": "direction", "op": "eq", "value": "OUT"}]}),
        Preset("no_order", "945 with no order", {"filters": [{"field": "shipments_without_order", "op": "gt", "value": "0"}]}),
    )


MAX_REFS = 50  # a Shopify page holds up to 250 orders: the first 50 and the count


def no_order_reason(d: Document) -> str:
    """Why a document belongs to no order or transfer, from what is stored about it."""
    status, kind = d.processing_status, d.kind
    if status == "Duplicate":
        return "Duplicate of a file already read"
    if status == "Failed":
        return f"Not read: {d.error}" if d.error else "Not read"
    if status == "Pending":
        return "Not read yet"
    if status == "Skipped":
        return d.error or "Skipped: not for Sales"
    if d.shadow:
        return "Shadow run: Zoho handled it"
    if kind == "997_EDI":
        return "997 for a file Sales did not send (Zoho's)"
    if kind == "945_EDI":
        return "945 for an order that is not in Sales" if d.shipments_without_order else "945 with no order"
    if kind == "SHOPIFY_ORDER_JSON":
        return "Its orders are before the store's start number, or were not imported"
    if kind == "850_JSON":
        return "No order was made from it"
    if kind == "OTHER":
        return "Not an order file"
    return "Not linked to an order"


class DocumentService(ReadOnlyService[Document, DocumentRead]):
    repo_cls = DocumentRepository
    read_schema = DocumentRead
    read_permission = READ

    async def enrich(self, rows: list[DocumentRead], docs) -> None:
        """Each row's orders (the first MAX_REFS and the count) and transfers, two queries for the page."""
        ids = [r.id for r in rows]
        s = self.uow.session
        ranked = (select(DocumentLink.document_id, Order.id, Order.source_reference, Order.order_number,
                         func.row_number().over(partition_by=DocumentLink.document_id, order_by=Order.id).label("n"),
                         func.count().over(partition_by=DocumentLink.document_id).label("total"))
                  .join(Order, Order.id == DocumentLink.entity_id)
                  .where(DocumentLink.document_id.in_(ids), DocumentLink.entity_type == "order").subquery())
        orders: dict[int, list[EntityRef]] = defaultdict(list)
        totals: dict[int, int] = {}
        for did, oid, ref, number, n, total in (await s.execute(select(ranked).where(ranked.c.n <= MAX_REFS))).all():
            orders[did].append(EntityRef(id=oid, label=ref or f"Internal no. {number}"))
            totals[did] = total
        pairs = union_all(select(Transfer.document_id.label("d"), Transfer.id.label("t")).where(Transfer.document_id.in_(ids)),
                          select(Receipt.document_id, Receipt.transfer_id).where(Receipt.document_id.in_(ids),
                                                                                  Receipt.transfer_id.is_not(None))).subquery()
        transfers: dict[int, list[EntityRef]] = defaultdict(list)
        for did, tid, number in (await s.execute(select(pairs.c.d, Transfer.id, Transfer.transfer_number)
                                                 .join(Transfer, Transfer.id == pairs.c.t).distinct())).all():
            transfers[did].append(EntityRef(id=tid, label=f"TO-{number}"))
        by_id = {d.id: d for d in docs}
        for r in rows:
            r.orders, r.orders_total, r.transfers = orders.get(r.id, []), totals.get(r.id, 0), transfers.get(r.id, [])
            r.no_order_reason = None if (r.orders or r.transfers) else no_order_reason(by_id[r.id])

    @requires(READ)
    async def raw(self, id: int) -> tuple[str, bytes]:
        """The original bytes (FR-OB-05). An identity row has none: it points
        at the document that holds them."""
        doc = await self.repo.get(id)
        content = (await self.uow.session.execute(select(Document.content).where(Document.id == id))).scalar_one()
        if content is None:
            original = (await self.uow.session.execute(select(Document.id).where(
                Document.sha256 == doc.sha256, Document.content.is_not(None)).limit(1))).scalar_one_or_none()
            raise BusinessError("document.no_content",
                                f"document {id} keeps no bytes of its own ({doc.processing_status})",
                                {"original_id": original}, status_code=404)
        return doc.remote_name or f"document-{id}", content


class RunRepository(Repository[Run]):
    model = Run
    list_fields = ("id", "job", "scope", "trigger", "status", "started_at", "heartbeat_at", "finished_at", "error")
    labels = {"scope": "For"}
    enums = {"trigger": RUN_TRIGGERS, "status": RUN_STATUSES}
    default_sort = ("-started_at",)
    groupable = ("scope", "job", "trigger", "status")
    presets = (Preset("all", "All"),
               Preset("failed", "Failed or partial", {"filters": [{"field": "status", "op": "in", "value": ["FAILED", "PARTIAL"]}]}),
               Preset("system", "System job", {"filters": [{"field": "scope", "op": "eq", "value": "system"}]}),
               Preset("connections", "Connection jobs", {"filters": [{"field": "job", "op": "eq", "value": "sales_connection"}]}))


class RunService(ReadOnlyService[Run, RunRead]):
    repo_cls = RunRepository
    read_schema = RunRead
    read_permission = READ

    @requires(FILE_RETRY)
    async def run_now(self, scheduler, background) -> int:
        """Everything now: each active connection's job, then the system job —
        each through its own single flight (AC-OB-46; a connection already
        running is skipped). Refused while the system job runs. Returns the
        system run's id."""
        run_id = await scheduler.system.claim(trigger="MANUAL", force=True)
        if run_id is None:
            raise BusinessError("run.already_running", "a cycle is running; wait for it to finish")
        background.spawn(scheduler.run_everything(run_id), name=f"sales-run-everything-{run_id}")
        return run_id

    @requires(FILE_RETRY)
    async def run_system_now(self, scheduler, background) -> int:
        """The system job alone, now (Health): never while it runs."""
        run_id = await scheduler.system.claim(trigger="MANUAL", force=True)
        if run_id is None:
            raise BusinessError("run.already_running", "the system job is running; wait for it to finish")
        background.spawn(scheduler.system.execute(run_id), name=f"sales-system-run-{run_id}")
        return run_id


class LogRepository(Repository[LogEntry]):
    model = LogEntry
    search_fields = ("message",)
    list_fields = ("id", "at", "run_id", "level", "step", "connection_id", "document_id", "order_id",
                   "transfer_id", "message")
    enums = {"level": LOG_LEVELS}
    default_sort = ("-at",)
    groupable = ("level", "step", "run_id", "connection_id")
    presets = (Preset("all", "All"), Preset("errors", "Errors", {"filters": [{"field": "level", "op": "eq", "value": "ERROR"}]}))


class LogService(ReadOnlyService[LogEntry, LogRead]):
    repo_cls = LogRepository
    read_schema = LogRead
    read_permission = READ


class DocumentActions:
    """The file register's actions (FR-OB-24 / 54 / 62; sales.file.retry): read a received file again,
    mark it ignored, or send a sent file again. A resend goes through the same rules as sending from its
    order, transfer or invoice, so it also needs that permission."""

    def __init__(self, uow) -> None:
        self.uow = uow
        self.s = uow.session

    async def _doc(self, id: int) -> Document:
        doc = await self.s.get(Document, id, with_for_update=True)
        if doc is None:
            raise NotFound("Document", id)
        return doc

    async def _resolve(self, tasks, doc: Document) -> None:
        for event in (UNREADABLE_FILE, ORDER_FILE_UNREADABLE):
            await tasks.resolve_problem(self.s, event, doc.id, by=self.uow.actor.user_id)

    @staticmethod
    def _stuck(doc: Document) -> bool:
        """Failed, or kept as a file nobody could read (an unknown kind still waiting)."""
        return doc.direction == "IN" and (doc.processing_status == InboundFile.FAILED.value
                                          or (doc.processing_status == InboundFile.PENDING.value and doc.kind == "OTHER"))

    @requires(FILE_RETRY)
    async def read_again(self, id: int, tasks) -> Document:
        """The file is classified again from its bytes and waits for the next pass."""
        doc = await self._doc(id)
        if not self._stuck(doc):
            raise BusinessError("document.not_failed", f"only a received file that failed can be read again (it is {doc.processing_status})")
        content = (await self.s.execute(select(Document.content).where(Document.id == id))).scalar_one()
        if content is None:
            raise BusinessError("document.no_content", "this record keeps no bytes of its own")
        c = classify(bytes(content))
        self.uow.set_reason("read again")
        doc.kind, doc.isa13, doc.gs06, doc.st02 = c.kind, c.isa13, c.gs06, c.st02
        doc.error = None if c.readable else c.problem
        if doc.processing_status == InboundFile.FAILED.value:
            INBOUND_FILE.transition(doc, InboundFile.PENDING, actor=self.uow.actor, events=self.uow.events, reason="read again")
        await self._resolve(tasks, doc)
        await self.s.flush()
        return doc

    @requires(FILE_RETRY)
    async def ignore(self, id: int, reason: str, tasks) -> Document:
        doc = await self._doc(id)
        if not self._stuck(doc):
            raise BusinessError("document.not_failed", f"only a received file that failed can be ignored (it is {doc.processing_status})")
        if len(reason.strip()) < 3:
            raise BusinessError("document.reason", "say why it is ignored", {"field": "reason"}, status_code=422)
        self.uow.set_reason(f"ignored: {reason.strip()}")
        if doc.processing_status == InboundFile.PENDING.value:
            INBOUND_FILE.transition(doc, InboundFile.PROCESSING, actor=self.uow.actor, events=self.uow.events, reason="ignored")
        INBOUND_FILE.transition(doc, InboundFile.SKIPPED, actor=self.uow.actor, events=self.uow.events, reason=reason.strip()[:200])
        doc.error = f"Ignored by {self.uow.actor.name}: {reason.strip()}"[:2000]
        await self._resolve(tasks, doc)
        await self.s.flush()
        return doc

    @requires(FILE_RETRY)
    async def send_again(self, id: int, rt) -> str:
        """A sent file again, as its owner would send it: the 940 of its order, the 943 of its transfer,
        the invoice. Each keeps its own rules (a live 940 is replaced only from the order)."""
        doc = await self._doc(id)
        if doc.direction != "OUT":
            raise BusinessError("document.not_outbound", "only a file we send can be sent again")
        if doc.kind == "940_EDI":
            await authorize(self.uow, "sales.order.operate")
            order_id = (await self.s.execute(select(DocumentLink.entity_id).where(
                DocumentLink.document_id == doc.id, DocumentLink.entity_type == "order"))).scalar_one()
            new = await rt.outbound.send_now(self.uow, order_id)
            return f"940 {new.st02} queued for the order"
        if doc.kind == "943_EDI":
            await authorize(self.uow, "sales.transfer.manage")
            await rt.transfers.resend(self.uow, int((doc.body_json or {})["transfer_id"]), ANY_VERSION)  # what is there now
            return "943 queued for the transfer"
        inv = (await self.s.execute(select(Invoice).where(Invoice.document_id == doc.id))).scalar_one_or_none()
        if inv is not None:
            await authorize(self.uow, "sales.invoice.manage")
            await rt.invoicing.send(self.uow, inv.id)
            return f"invoice {inv.invoice_number} queued"
        raise BusinessError("document.not_resendable", f"a {doc.kind} is not sent again from here")
