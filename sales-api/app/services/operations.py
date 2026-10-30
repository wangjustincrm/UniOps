"""The document register, runs and log (FR-OB-53): read with sales.read.
"Run now" (design §4.1) claims a run through the scheduler's own single-flight
path and needs sales.file.retry."""
from __future__ import annotations

from sqlalchemy import select

from app.domain.documents import DIRECTIONS, KINDS, LOG_LEVELS, ORIGINS, RUN_STATUSES, RUN_TRIGGERS, SYSTEMS
from app.domain.status import Ack, InboundFile, OutboundFile
from app.models.documents import Document, LogEntry, Run
from app.platform.authorization import requires
from app.platform.errors import BusinessError
from app.platform.repository import Repository
from app.platform.services import ReadOnlyService
from app.schemas.operations import DocumentRead, LogRead, RunRead

READ = "sales.read"
FILE_RETRY = "sales.file.retry"


class DocumentRepository(Repository[Document]):
    model = Document
    search_fields = ("remote_name", "sha256", "error")
    list_fields = ("id", "direction", "origin", "system", "connection_id", "kind", "remote_name", "size",
                   "remote_mtime", "sha256", "processing_status", "file_status", "ack_status", "isa13", "gs06",
                   "st02", "partner_code", "error", "created_at")
    labels = {"remote_name": "File", "remote_mtime": "Modified on the server", "isa13": "ISA13", "gs06": "GS06",
              "st02": "ST02", "sha256": "SHA-256"}
    enums = {"direction": DIRECTIONS, "origin": ORIGINS, "system": SYSTEMS, "kind": KINDS,
             "processing_status": tuple(s.value for s in InboundFile),
             "file_status": tuple(s.value for s in OutboundFile), "ack_status": tuple(s.value for s in Ack)}
    default_sort = ("-created_at",)


class DocumentService(ReadOnlyService[Document, DocumentRead]):
    repo_cls = DocumentRepository
    read_schema = DocumentRead
    read_permission = READ

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
    list_fields = ("id", "job", "trigger", "status", "started_at", "heartbeat_at", "finished_at", "error")
    enums = {"trigger": RUN_TRIGGERS, "status": RUN_STATUSES}
    default_sort = ("-started_at",)


class RunService(ReadOnlyService[Run, RunRead]):
    repo_cls = RunRepository
    read_schema = RunRead
    read_permission = READ

    @requires(FILE_RETRY)
    async def run_now(self, cycle, background) -> int:
        """Claims through the same path as the scheduler: never a second
        cycle while one runs (AC-OB-46)."""
        run_id = await cycle.claim(trigger="MANUAL", force=True)
        if run_id is None:
            raise BusinessError("run.already_running", "a cycle is running; wait for it to finish")
        background.spawn(cycle.execute(run_id), name=f"sales-cycle-run-{run_id}")
        return run_id


class LogRepository(Repository[LogEntry]):
    model = LogEntry
    search_fields = ("message",)
    list_fields = ("id", "at", "run_id", "level", "step", "connection_id", "document_id", "order_id",
                   "transfer_id", "message")
    enums = {"level": LOG_LEVELS}
    default_sort = ("-at",)


class LogService(ReadOnlyService[LogEntry, LogRead]):
    repo_cls = LogRepository
    read_schema = LogRead
    read_permission = READ
