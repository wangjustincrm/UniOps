"""The transfer endpoints' service: sales.read to look, sales.transfer.manage
for every change. The work is in app/services/transfers.py."""
from __future__ import annotations

from sqlalchemy import select

from app.domain.status import TRANSFER
from app.domain.status import Transfer as T
from app.models.config import Warehouse
from app.models.documents import Document
from app.models.transfers import Receipt, ReceiptRow, Transfer, TransferLine
from app.platform.authorization import requires
from app.platform.commands import ANY_VERSION, VersionArg, load_for_command
from app.platform.errors import BusinessError
from app.platform.repository import Repository
from app.platform.services import ReadOnlyService
from app.platform.views import Preset
from app.services import oplog, print_docs
from app.services.record_files import documents_of, timeline_of
from app.schemas.transfers import ReceiptRead, ReceiptRowRead, TransferDetail, TransferLineRead, TransferRead

READ = "sales.read"
MANAGE = "sales.transfer.manage"


class TransferRepository(Repository[Transfer]):
    model = Transfer
    search_fields = ("transfer_number", "notes")
    list_fields = ("id", "transfer_number", "from_warehouse_id", "to_warehouse_id", "status", "items", "sent_ea", "received_ea",
                   "receipt_date", "attachments", "notes", "error", "created_at", "updated_at")
    labels = {"transfer_number": "Transfer", "from_warehouse_id": "From", "to_warehouse_id": "To", "sent_ea": "Sent EA",
              "received_ea": "Received EA", "receipt_date": "Received on", "items": "Items", "attachments": "Attachments"}
    enums = {"status": tuple(s.value for s in T)}
    default_sort = ("-created_at", "-transfer_number")
    groupable = ("status", "from_warehouse_id", "to_warehouse_id", "receipt_date")
    summable = ("sent_ea", "received_ea")
    suggestable = ("transfer_number",)
    presets = (
        Preset("all", "All"),
        Preset("open", "Open", {"filters": [{"field": "status", "op": "in", "value": [
            "Draft", "Ready To Send", "943 Generated", "Waiting for 997", "Waiting for 944"]}]}),
        Preset("attention", "Needs attention", {"filters": [{"field": "status", "op": "in", "value": [
            "Failed", "Discrepancy", "Partially Received", "On Hold"]}]}),
    )


class TransferService(ReadOnlyService[Transfer, TransferRead]):
    repo_cls = TransferRepository
    read_schema = TransferRead
    read_permission = READ

    @requires(READ)
    async def pdf(self, transfer_id: int) -> tuple[str, bytes]:
        t = await self.repo.get(transfer_id)
        return f"TO-{t.transfer_number}.pdf", await print_docs.transfer_pdf(self.uow.session, t)

    async def enrich(self, items: list[TransferRead], entities) -> None:
        """The warehouses' codes."""
        ids = {i.from_warehouse_id for i in items} | {i.to_warehouse_id for i in items}
        codes = dict((await self.uow.session.execute(select(Warehouse.id, Warehouse.code).where(Warehouse.id.in_(ids)))).all()) if ids else {}
        for i in items:
            i.from_warehouse, i.to_warehouse = codes.get(i.from_warehouse_id), codes.get(i.to_warehouse_id)

    @requires(READ)
    async def detail(self, id: int) -> TransferDetail:
        s = self.uow.session
        t = await self.repo.get(id)
        base = await self._read_one(t)
        lines = (await s.execute(select(TransferLine).where(TransferLine.transfer_id == id).order_by(TransferLine.line_no))).scalars()
        receipts = list((await s.execute(select(Receipt).where(Receipt.transfer_id == id).order_by(Receipt.id))).scalars())
        rows = list((await s.execute(select(ReceiptRow).where(ReceiptRow.receipt_id.in_([r.id for r in receipts]))
                                     .order_by(ReceiptRow.receipt_id, ReceiptRow.position))).scalars()) if receipts else []
        names = dict((await s.execute(select(Document.id, Document.remote_name).where(
            Document.id.in_([r.document_id for r in receipts])))).all()) if receipts else {}
        doc = await s.get(Document, t.document_id) if t.document_id else None
        return TransferDetail(
            **base.model_dump(), lines=[TransferLineRead.model_validate(x) for x in lines],
            receipts=[ReceiptRead.model_validate(r).model_copy(update={
                "document_name": names.get(r.document_id),
                "rows": [ReceiptRowRead.model_validate(x) for x in rows if x.receipt_id == r.id]}) for r in receipts],
            ack_status=doc.ack_status if doc else None, file_status=doc.file_status if doc else None,
            control=doc.st02 if doc else None, status_before_hold=t.status_before_hold,
            documents=await documents_of(s, "transfer", id), timeline=await timeline_of(s, "transfer", id))

    async def _after(self, id: int) -> TransferDetail:
        await self.uow.session.flush()
        return await TransferService.detail.__wrapped__(self, id)  # already authorised

    @requires(MANAGE)
    async def create(self, transfers, body) -> TransferDetail:
        t = await transfers.create(self.uow, from_id=body.from_warehouse_id, to_id=body.to_warehouse_id,
                                   lines=[x.model_dump() for x in body.lines], notes=body.notes)
        return await self._after(t.id)

    @requires(MANAGE)
    async def update(self, transfers, id: int, body, version: int | None) -> TransferDetail:
        await transfers.update(self.uow, id, version=version, from_id=body.from_warehouse_id, to_id=body.to_warehouse_id,
                               lines=[x.model_dump() for x in body.lines], notes=body.notes)
        return await self._after(id)

    @requires(MANAGE)
    async def ready(self, transfers, id: int, version: int | None) -> TransferDetail:
        await transfers.ready(self.uow, id, version)
        return await self._after(id)

    @requires(MANAGE)
    async def cancel(self, transfers, id: int, version: int | None, reason: str) -> TransferDetail:
        await transfers.cancel(self.uow, id, version, reason)
        return await self._after(id)

    @requires(MANAGE)
    async def resend(self, transfers, id: int, version: int | None) -> TransferDetail:
        await transfers.resend(self.uow, id, version)
        return await self._after(id)

    @requires(MANAGE)
    async def release(self, id: int, reason: str, version: VersionArg = ANY_VERSION) -> TransferDetail:
        """On Hold (a second, different 944: compared by a person) back to where it was, with why (as an order's hold).
        The first 944 stays applied; the second stays kept, not applied."""
        t = await load_for_command(self.uow.session, Transfer, id, version=version)
        if t.status != T.ON_HOLD.value or not t.status_before_hold:
            raise BusinessError("transfer.not_on_hold", f"transfer {t.transfer_number} is not On Hold")
        back = T(t.status_before_hold)
        self.uow.set_reason(f"hold released to {back.value}: {reason}")
        TRANSFER.transition(t, back, actor=self.uow.actor, events=self.uow.events, reason=reason)
        await oplog.write(self.uow.session, "INFO", "transfer", f"transfer {t.transfer_number} released to {back.value}: {reason}",
                          transfer_id=t.id)
        return await self._after(id)
