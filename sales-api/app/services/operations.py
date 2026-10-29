"""The document register, runs and log (FR-OB-53): read with sales.read.
"Run now" (design §4.1) claims a run through the scheduler's own single-flight
path and needs sales.file.retry."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.models.documents import Document, LogEntry, Run
from app.platform.authorization import requires
from app.platform.errors import BusinessError
from app.platform.repository import FilterRule, Repository, parse_bool  # noqa: F401
from app.platform.services import ReadOnlyService
from app.schemas.operations import DocumentRead, LogRead, RunRead

READ = "sales.read"
FILE_RETRY = "sales.file.retry"


class DocumentRepository(Repository[Document]):
    model = Document
    search_fields = ("remote_name", "sha256", "error")
    sortable = {"id": Document.id, "created_at": Document.created_at, "remote_name": Document.remote_name,
                "kind": Document.kind, "processing_status": Document.processing_status, "size": Document.size}
    filterable = {
        "direction": FilterRule(Document.direction), "kind": FilterRule(Document.kind),
        "system": FilterRule(Document.system), "connection_id": FilterRule(Document.connection_id),
        "processing_status": FilterRule(Document.processing_status),
        "created_at": FilterRule(Document.created_at, ops=frozenset({"range"}), cast=datetime.fromisoformat),
    }
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
    sortable = {"id": Run.id, "started_at": Run.started_at, "status": Run.status}
    filterable = {"status": FilterRule(Run.status), "trigger": FilterRule(Run.trigger), "job": FilterRule(Run.job)}
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
    sortable = {"id": LogEntry.id, "at": LogEntry.at, "level": LogEntry.level}
    filterable = {
        "level": FilterRule(LogEntry.level), "step": FilterRule(LogEntry.step),
        "run_id": FilterRule(LogEntry.run_id), "connection_id": FilterRule(LogEntry.connection_id),
        "document_id": FilterRule(LogEntry.document_id), "order_id": FilterRule(LogEntry.order_id),
        "at": FilterRule(LogEntry.at, ops=frozenset({"range"}), cast=datetime.fromisoformat),
    }
    default_sort = ("-at",)


class LogService(ReadOnlyService[LogEntry, LogRead]):
    repo_cls = LogRepository
    read_schema = LogRead
    read_permission = READ
