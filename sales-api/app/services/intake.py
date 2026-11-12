"""SFTP intake (FR-OB-70..76, design §4.3): one pass over one connection.

For each listed file, oldest first:

    already here (connection, name, size, mtime)  → skipped, not downloaded
    younger than stable_seconds                   → too new, read next pass
    download → sha256 → classify by content
    not ours (FR-OB-75)        → identity row (Skipped, no content); left alone
    bytes already stored       → identity row (Duplicate, no content)
    otherwise                  → stored (Pending), with its original bytes
    → COMMIT → after-read action (Leave / Move / Delete) — only for files we
      read (stored or duplicate); its failure is only logged (FR-OB-72)

Each file is its own unit (`isolated`): one bad file never stops the pass. A
file that cannot be stored goes to the error folder whatever the policy
(FR-OB-74). A write the outbound guard refuses (switch off, or a TEST
connection) is `Suppressed`: logged as "would have written" and treated as
Leave (§5.5). A refused login latches the connection (FR-OB-50): it is skipped
until a user clears it. Parsing is the next step's job (P3 onward): stored
documents stay Pending.
"""
from __future__ import annotations

import hashlib
import posixpath
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.intake import classify
from app.domain.status import InboundFile
from app.integrations.sftp import RemoteFile, SftpAuthFailed, readable
from app.models.config import Connection
from app.models.documents import Document
from app.platform.actors import SYSTEM_ACTOR
from app.platform.guard import Suppressed
from app.platform.pipeline import UnitOutcome, isolated
from app.services import oplog
from app.services.settings import SETTINGS, SHADOW_MODE
from app.services.problems import FILE_NOT_STORED, SFTP_AUTH_FAILED, SFTP_FETCH_FAILED, UNREADABLE_FILE, problem

STEP = "intake"


@dataclass
class PassReport:
    """FR-OB-76: listed equals the sum of the other six (AC-OB-09)."""
    listed: int = 0
    too_new: int = 0
    already_here: int = 0
    not_ours: int = 0
    stored: int = 0
    duplicates: int = 0
    failed: int = 0
    status: str = "ok"  # ok | latched | list_failed | read_off

    @property
    def balanced(self) -> bool:
        return self.listed == self.too_new + self.already_here + self.not_ours + self.stored + self.duplicates + self.failed

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConnectionSnapshot:
    """What a pass needs from the connection row, read once: the gateway gets
    this, never a session-bound row."""
    id: int
    name: str
    kind: str
    host: str | None
    port: int | None
    username: str | None
    secret_name: str | None
    host_key: str | None
    can_read: bool
    can_write: bool
    inbound_folder: str | None
    processed_folder: str | None
    error_folder: str | None
    after_read: str
    stable_seconds: int
    auth_failed_at: datetime | None
    # Shopify (sales04)
    api_version: str | None = None
    cursor_updated_at: datetime | None = None
    page_size: int | None = None
    order_number_cutoff: int | None = None
    partner_code: str | None = None
    # Outbound (P4): where 940s, 943s and 810s are put, when writing is on.
    outbound_folder: str | None = None
    # The shadow run (§5.4), read at the start of a pass: after-read is Leave
    # and every stored document is flagged.
    shadow: bool = False

    @classmethod
    def of(cls, c: Connection) -> ConnectionSnapshot:
        return cls(c.id, c.name, c.kind, c.host, c.port, c.username, c.secret_name, c.host_key, c.can_read, c.can_write,
                   c.inbound_folder, c.processed_folder, c.error_folder, c.after_read, c.stable_seconds,
                   c.auth_failed_at, c.api_version, c.cursor_updated_at, c.page_size, c.order_number_cutoff,
                   c.partner_code, c.outbound_folder)


class Intake:
    def __init__(self, *, uow_factory, sftp, tasks, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.uow_factory = uow_factory
        self.sftp = sftp
        self.tasks = tasks
        self.clock = clock

    async def _log(self, level: str, message: str, conn: ConnectionSnapshot, run_id: int | None, **context: Any) -> None:
        await oplog.write_alone(self.uow_factory, level, STEP, message, run_id=run_id, connection_id=conn.id,
                                context=context or None)

    async def _raise(self, event: str, entity: Any, title: str, description: str) -> None:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            await self.tasks.raise_problem(uow.session, event, entity, problem(event, title, description))
            await uow.commit()

    # ── one pass ──────────────────────────────────────────────────────────────
    async def run_pass(self, conn: ConnectionSnapshot, *, run_id: int | None = None) -> PassReport:
        report = PassReport()
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            conn = replace(conn, shadow=await SETTINGS.get(uow.session, SHADOW_MODE))
        if conn.auth_failed_at is not None:
            report.status = "latched"  # reported once, when it latched (AC-OB-48)
            return report
        if not conn.can_read:
            report.status = "read_off"  # the connection's Read tick is off: nothing is listed
            return report
        if not conn.inbound_folder:
            report.status = "list_failed"
            await self._log("ERROR", f"{conn.name}: no inbound folder configured", conn, run_id)
            return report
        try:
            files = await self.sftp.list(conn, conn.inbound_folder)
        except SftpAuthFailed as e:
            await self._latch(conn, str(e), run_id)
            report.status = "latched"
            return report
        except Exception as e:  # noqa: BLE001 — one unreachable partner never stops the cycle (AC-OB-47)
            report.status = "list_failed"
            why = readable(e)
            await self._log("ERROR", f"{conn.name}: listing {conn.inbound_folder} failed: {why}", conn, run_id,
                            error_type=type(e).__name__)
            await self._raise(SFTP_FETCH_FAILED, conn.id, f"SFTP fetch failed: {conn.name}",
                              f"Listing {conn.inbound_folder} failed: {why}")
            return report

        report.listed = len(files)
        cutoff = self.clock() - timedelta(seconds=conn.stable_seconds)
        oldest_first = sorted(files, key=lambda f: (f.modified_at or cutoff, f.name))
        for f in oldest_first:
            outcome = await self._one(conn, f, cutoff, run_id)
            if isinstance(outcome, str):
                setattr(report, outcome, getattr(report, outcome) + 1)
            else:  # the gateway refused the login part-way through the pass
                await self._latch(conn, str(outcome), run_id)
                report.failed += report.listed - sum(
                    getattr(report, k) for k in ("too_new", "already_here", "not_ours", "stored", "duplicates", "failed"))
                report.status = "latched"
                break
        return report

    async def _one(self, conn: ConnectionSnapshot, f: RemoteFile, cutoff: datetime, run_id: int | None):
        if await self._already_here(conn, f):
            return "already_here"
        if f.modified_at is not None and f.modified_at > cutoff:
            return "too_new"  # still arriving (FR-OB-73)
        try:
            data = await self.sftp.get(conn, f.path)
        except SftpAuthFailed as e:
            return e
        except Exception as e:  # noqa: BLE001
            await self._not_stored(conn, f, f"download failed: {e}", run_id)
            return "failed"

        sha = hashlib.sha256(data).hexdigest()
        c = classify(data)
        outcome = await isolated(
            f"{conn.name}:{f.name}", lambda uow: self._store(uow, conn, f, data, sha, c, run_id),
            uow_factory=self.uow_factory, log=self._store_failed(conn, f, run_id),
        )
        if not outcome.ok:
            await self._not_stored(conn, f, f"store failed: {outcome.message}", run_id, log=False)
            return "failed"
        result, doc_id = outcome.result
        if result in ("stored", "duplicates"):
            await self._after_read(conn, f, doc_id, run_id)
        return result

    async def _already_here(self, conn: ConnectionSnapshot, f: RemoteFile) -> bool:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            return (await uow.session.execute(select(Document.id).where(
                Document.direction == "IN", Document.connection_id == conn.id, Document.remote_name == f.name,
                Document.size == f.size, Document.remote_mtime == f.modified_at,
            ).limit(1))).scalar_one_or_none() is not None

    async def _store(self, uow, conn: ConnectionSnapshot, f: RemoteFile, data: bytes, sha: str, c, run_id):
        s = uow.session
        row = dict(direction="IN", origin="PARTNER", system=c.system, connection_id=conn.id, kind=c.kind,
                   remote_name=f.name, size=f.size, remote_mtime=f.modified_at, sha256=sha,
                   isa13=c.isa13, gs06=c.gs06, st02=c.st02, shadow=conn.shadow)
        if not c.ours:
            doc = Document(**row, content=None, processing_status=InboundFile.SKIPPED.value,
                           error=f"not ours: {c.kind}")
            s.add(doc)
            await s.flush()
            await oplog.write(s, "INFO", STEP, f"{conn.name}: {f.name} is not ours ({c.kind}); left alone",
                              run_id=run_id, connection_id=conn.id, document_id=doc.id)
            return "not_ours", doc.id
        original = await self._original(s, sha)
        if original is None:
            try:
                async with s.begin_nested():
                    doc = Document(**row, content=data, processing_status=InboundFile.PENDING.value,
                                   error=c.problem)
                    s.add(doc)
                    await s.flush()
            except IntegrityError:
                original = await self._original(s, sha)  # stored by a concurrent pass meanwhile
                if original is None:
                    raise
        if original is not None:
            doc = Document(**row, content=None, processing_status=InboundFile.DUPLICATE.value,
                           error=f"same bytes as document {original}")
            s.add(doc)
            await s.flush()
            await oplog.write(s, "INFO", STEP, f"{conn.name}: {f.name} duplicates document {original}",
                              run_id=run_id, connection_id=conn.id, document_id=doc.id)
            return "duplicates", doc.id
        await oplog.write(s, "INFO", STEP, f"{conn.name}: stored {f.name} as {c.kind} ({f.size} bytes)",
                          run_id=run_id, connection_id=conn.id, document_id=doc.id)
        if not c.readable:  # stored as it is, and someone looks at it (FR-OB-70, AC-OB-01)
            await self.tasks.raise_problem(s, UNREADABLE_FILE, doc.id, problem(
                UNREADABLE_FILE, f"Unreadable file {f.name}"[:255],
                f"{conn.name}: {f.name} was stored as document {doc.id} but cannot be read: {c.problem}"))
        return "stored", doc.id

    @staticmethod
    async def _original(session, sha: str) -> int | None:
        return (await session.execute(
            select(Document.id).where(Document.sha256 == sha, Document.content.is_not(None)).limit(1)
        )).scalar_one_or_none()

    def _store_failed(self, conn: ConnectionSnapshot, f: RemoteFile, run_id: int | None):
        async def log(o: UnitOutcome) -> None:
            await self._log("ERROR", f"{conn.name}: {f.name} could not be stored: {o.message}", conn, run_id,
                            file=f.name, error_type=o.error_type)
        return log

    async def _not_stored(self, conn: ConnectionSnapshot, f: RemoteFile, why: str, run_id: int | None,
                          *, log: bool = True) -> None:
        """FR-OB-74: to the error folder whatever the policy (the guard may refuse)."""
        if log:
            await self._log("ERROR", f"{conn.name}: {f.name}: {why}", conn, run_id, file=f.name)
        await self._raise(FILE_NOT_STORED, conn.id, f"File could not be stored: {conn.name}",
                          f"{f.name}: {why}")
        if conn.shadow:  # Zoho must still find it where it was
            await self._log("INFO", f"{conn.name}: shadow run: {f.name} left in place (not moved to the error folder)",
                            conn, run_id, file=f.name)
        elif conn.error_folder:
            await self._remote_write(conn, f, "move", posixpath.join(conn.error_folder, f.name), None, run_id)

    async def _after_read(self, conn: ConnectionSnapshot, f: RemoteFile, doc_id: int, run_id: int | None) -> None:
        if conn.shadow:  # §5.4: Leave whatever the connection says — Zoho reads the same folder
            if conn.after_read != "LEAVE":
                await oplog.write_alone(self.uow_factory, "INFO", STEP,
                                        f"{conn.name}: shadow run: {f.name} left in place (after-read {conn.after_read} not applied)",
                                        run_id=run_id, connection_id=conn.id, document_id=doc_id)
            return
        if conn.after_read == "MOVE" and conn.processed_folder:
            await self._remote_write(conn, f, "move", posixpath.join(conn.processed_folder, f.name), doc_id, run_id)
        elif conn.after_read == "DELETE":
            await self._remote_write(conn, f, "delete", None, doc_id, run_id)

    async def _remote_write(self, conn, f: RemoteFile, op: str, dst: str | None, doc_id, run_id) -> None:
        """An after-read write never fails the pass (FR-OB-72). With the Write tick off the file is
        left where it is, as Leave would (no attempt: the guard would only refuse it)."""
        if not conn.can_write:
            return
        try:
            if op == "move":
                await self.sftp.move(conn, f.path, dst)
            else:
                await self.sftp.delete(conn, f.path)
        except Suppressed as e:
            await oplog.write_alone(self.uow_factory, "INFO", STEP,
                                    f"{conn.name}: {op} of {f.name} refused ({e}); treated as Leave",
                                    run_id=run_id, connection_id=conn.id, document_id=doc_id)
        except Exception as e:  # noqa: BLE001
            await oplog.write_alone(self.uow_factory, "WARNING", STEP,
                                    f"{conn.name}: {op} of {f.name} failed: {e}; the document stays stored",
                                    run_id=run_id, connection_id=conn.id, document_id=doc_id)

    async def _latch(self, conn: ConnectionSnapshot, reason: str, run_id: int | None) -> None:
        """FR-OB-50: report once and skip the connection until a user clears it."""
        async with self.uow_factory(SYSTEM_ACTOR, reason="sftp auth failed") as uow:
            row = await uow.session.get(Connection, conn.id, with_for_update=True)
            row.auth_failed_at = self.clock()
            row.auth_failed_reason = reason[:2000]
            await oplog.write(uow.session, "ERROR", STEP, f"{conn.name}: login refused; connection latched: {reason}",
                              run_id=run_id, connection_id=conn.id)
            await self.tasks.raise_problem(uow.session, SFTP_AUTH_FAILED, conn.id, problem(
                SFTP_AUTH_FAILED, f"SFTP login refused: {conn.name}",
                f"{reason}. The connection is skipped until someone clears it on the Connections page.", urgent=True))
            await uow.commit()
