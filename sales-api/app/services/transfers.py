"""Stock transfers (FR-OB-80 … 84; P7): Draft → Ready To Send → the 943 →
the 997 → the 944.

- `ready` (a person) → the cycle's `queue_ready` builds the 943 (A.4; the
  shared control sequence; checked first, FR-OB-29a) and queues ONE put
  (outbox `send_943`, through the guarded SFTP path: switch + LIVE + write
  host); a 943 that fails its checks or whose put fails or is refused makes
  the transfer Failed (FR-OB-81) — resent or cancelled by a person.
- The 997 (FR-OB-82, from the 997 step): accepted → Waiting for 944;
  rejected → Failed with a task carrying the AK5 code, never resent by
  itself. No 997 in time → the ACK times out with a task; the transfer stays
  Waiting for 997.
- The 944 (FR-OB-83 / 84): matched by W17-04; every W07 allocated (A.7),
  repeated pallets summed, never overwritten; Received / Partially Received
  (logged; P8 notifies) / Discrepancy (task listing the lines). One 944 per
  transfer: a second, different one is stored, not applied, and puts the
  transfer On Hold with a task; one that matches no transfer is stored with
  a task. A 944 before the 997 counts as the acceptance.
- The shadow run: no 943 is built or put (generate refuses; the outbox
  pre_check cancels); a 944 is stored and compared with the Zoho 943 it
  answers, never applied, no task.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, select, text

from app.domain.edi943 import Line943, Party943, Transfer943, build_943
from app.domain.edi944 import LineIn, allocate, parse_944
from app.domain.status import ACK, INBOUND_FILE, OUTBOUND_FILE, TRANSFER, Ack, InboundFile, OutboundFile
from app.domain.status import Transfer as T
from app.domain.x12 import X12Error
from app.models.config import Connection, Item, Warehouse
from app.models.documents import Document, DocumentLink
from app.models.records import OutboxRecord
from app.models.transfers import Receipt, ReceiptRow, Transfer, TransferLine
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.outbox import ONCE, Outbox, OutboxKind
from app.platform.pipeline import isolated
from app.services import oplog
from app.services.intake import ConnectionSnapshot
from app.services.outbound import TORONTO
from app.services.problems import (
    EDI_943_REJECTED, EDI_943_TIMEOUT, EDI_944_NO_TRANSFER, ORDER_FILE_UNREADABLE, PARTIAL_RECEIPT, SECOND_944,
    TRANSFER_DISCREPANCY, TRANSFER_FAILED, problem, send_refused,
)
from app.services.settings import ACK_TIMEOUT_MINUTES, EDI_CONNECTION_ID, SETTINGS, SHADOW_MODE

logger = logging.getLogger(__name__)

STEP = "transfer"
SEND_943 = "send_943"
OUTBOX = Outbox(OutboxRecord)
CANCELLABLE = (T.DRAFT, T.READY_TO_SEND, T.GENERATED_943, T.FAILED)


@dataclass
class TransferReport:
    generated: int = 0
    invalid: int = 0
    receipts: int = 0
    second: int = 0
    no_transfer: int = 0
    shadow: int = 0
    timeouts: int = 0
    failed: int = 0
    skipped: str | None = None
    detail: list[str] = field(default_factory=list)


def _party(w: Warehouse) -> Party943:
    return Party943(w.name, w.edi_id_qualifier, w.edi_id, w.address1, w.city, w.province, w.zip, w.country)


def _key(v: str | None) -> str:
    v = (v or "").strip()
    return str(int(v)) if v.isdigit() else v


class Transfers:
    def __init__(self, *, uow_factory, tasks, sftp, shadow=None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.uow_factory = uow_factory
        self.tasks = tasks
        self.sftp = sftp
        self.shadow = shadow  # the shadow-run matcher (ShadowRun), set by the runtime
        self.clock = clock

    # ── a person's actions (the service checks sales.transfer.manage) ────────
    async def create(self, uow, *, from_id: int, to_id: int, lines: list[dict[str, Any]], notes: str | None) -> Transfer:
        s = uow.session
        await self._check(s, from_id, to_id, lines)
        number = (await s.execute(text("SELECT nextval('sales_transfer_number_seq')"))).scalar_one()
        uow.set_reason("transfer created")
        t = Transfer(transfer_number=f"{number:05d}", from_warehouse_id=from_id, to_warehouse_id=to_id, status=T.DRAFT.value,
                     notes=notes, version=1)
        s.add(t)
        await s.flush()
        self._lines(s, t, lines)
        await s.flush()
        return t

    async def update(self, uow, id: int, *, version: int | None, from_id: int, to_id: int, lines: list[dict[str, Any]],
                     notes: str | None) -> Transfer:
        s = uow.session
        t = await self._get(s, id, version)
        if t.status != T.DRAFT.value:
            raise BusinessError("transfer.not_draft", "only a Draft transfer can be changed")
        await self._check(s, from_id, to_id, lines)
        uow.set_reason("transfer edited")
        t.from_warehouse_id, t.to_warehouse_id, t.notes = from_id, to_id, notes
        for old in (await s.execute(select(TransferLine).where(TransferLine.transfer_id == t.id))).scalars():
            await s.delete(old)
        await s.flush()
        self._lines(s, t, lines)
        await s.flush()
        return t

    @staticmethod
    def _lines(s, t: Transfer, lines: list[dict[str, Any]]) -> None:
        for i, ln in enumerate(lines, start=1):
            s.add(TransferLine(transfer_id=t.id, line_no=i, item_code=ln["item_code"], qty_ea=int(ln["qty_ea"])))

    async def _check(self, s, from_id: int, to_id: int, lines: list[dict[str, Any]]) -> None:
        if from_id == to_id:
            raise BusinessError("transfer.same_warehouse", "the warehouses must differ")
        for wid in (from_id, to_id):
            w = await s.get(Warehouse, wid)
            if w is None or not w.active:
                raise BusinessError("transfer.warehouse", f"warehouse {wid} is not an active warehouse")
        if not lines:
            raise BusinessError("transfer.no_lines", "a transfer needs at least one line")
        codes = {ln["item_code"] for ln in lines}
        found = set((await s.execute(select(Item.item_code).where(Item.item_code.in_(codes), Item.active.is_(True)))).scalars())
        missing = sorted(codes - found)
        if missing:
            raise BusinessError("transfer.item", f"not an active item: {', '.join(missing)}")
        if any(int(ln["qty_ea"]) <= 0 for ln in lines):
            raise BusinessError("transfer.quantity", "each line needs a quantity of at least 1 EA")

    async def _get(self, s, id: int, version: int | None = None) -> Transfer:
        t = await s.get(Transfer, id, with_for_update=True)
        if t is None:
            raise BusinessError("transfer.not_found", "no such transfer", status_code=404)
        if version is not None and t.version != version:
            raise BusinessError("entity.stale", "someone else changed this transfer", status_code=409)
        return t

    async def ready(self, uow, id: int, version: int | None) -> Transfer:
        t = await self._get(uow.session, id, version)
        uow.set_reason("transfer ready to send")
        TRANSFER.transition(t, T.READY_TO_SEND, actor=uow.actor, events=uow.events, reason="ready to send (FR-OB-80)")
        await uow.session.flush()
        return t

    async def cancel(self, uow, id: int, version: int | None, reason: str) -> Transfer:
        """FR-OB-80 / AC-OB-69: before the 943 is sent, or after it was rejected."""
        s = uow.session
        t = await self._get(s, id, version)
        if T(t.status) not in CANCELLABLE:
            raise BusinessError("transfer.not_cancellable", f"a transfer in {t.status} cannot be cancelled: the 943 is sent")
        uow.set_reason(f"transfer cancelled: {reason}")
        if t.status == T.GENERATED_943.value:  # queued, not sent: the row and the file stop here
            await OUTBOX.cancel_for(s, t, "transfer cancelled")
            doc = await s.get(Document, t.document_id, with_for_update=True) if t.document_id else None
            if doc is not None and doc.file_status in (OutboundFile.READY_TO_SEND.value, OutboundFile.SUPPRESSED.value):
                OUTBOUND_FILE.transition(doc, OutboundFile.CANCELLED, actor=uow.actor, events=uow.events, reason="transfer cancelled")
        TRANSFER.transition(t, T.CANCELLED, actor=uow.actor, events=uow.events, reason=reason)
        await self.tasks.resolve_problem(s, TRANSFER_FAILED, t.id, by=uow.actor.user_id)
        await self.tasks.resolve_problem(s, EDI_943_REJECTED, t.id, by=uow.actor.user_id)
        await s.flush()
        return t

    async def resend(self, uow, id: int, version: int | None) -> Transfer:
        """FR-OB-81 / FR-OB-24: a Failed transfer gets a new 943 (a new control number)."""
        s = uow.session
        t = await self._get(s, id, version)
        if t.status != T.FAILED.value:
            raise BusinessError("transfer.not_failed", "only a Failed transfer is resent")
        uow.set_reason("943 resent")
        await self.generate(uow, t, reason="resend (FR-OB-81)")
        await self.tasks.resolve_problem(s, EDI_943_REJECTED, t.id, by=uow.actor.user_id)
        await s.flush()
        return t

    # ── the 943 ──────────────────────────────────────────────────────────────
    async def _connection(self, s) -> Connection | None:
        cid = await SETTINGS.get(s, EDI_CONNECTION_ID)
        c = await s.get(Connection, cid) if cid else None
        return c if c is not None and c.kind == "SFTP" and c.active else None

    async def generate(self, uow, t: Transfer, *, reason: str) -> Document:
        s = uow.session
        if await SETTINGS.get(s, SHADOW_MODE):
            raise BusinessError("transfer.shadow", "the shadow run is on: Sales sends no 943 (Zoho is live)")
        conn = await self._connection(s)
        if conn is None:
            raise BusinessError("edi.no_connection", "no warehouse connection is set (setting edi.warehouse_connection_id)")
        frm, to = await s.get(Warehouse, t.from_warehouse_id), await s.get(Warehouse, t.to_warehouse_id)
        lines = list((await s.execute(select(TransferLine).where(TransferLine.transfer_id == t.id)
                                      .order_by(TransferLine.line_no))).scalars())
        weights = dict((await s.execute(select(Item.item_code, Item.gross_weight_lb_per_ea).where(
            Item.item_code.in_([ln.item_code for ln in lines])))).all())
        ctl = (await s.execute(text("SELECT nextval('sales_x12_control_seq')"))).scalar_one()
        built = build_943(Transfer943(t.transfer_number, _party(frm), _party(to), tuple(
            Line943(ln.line_no, ln.item_code, ln.qty_ea, weights.get(ln.item_code)) for ln in lines)),
            ctl, self.clock().astimezone(TORONTO))
        doc = Document(direction="OUT", origin="UNIOPS", system="RADIANT", connection_id=conn.id, kind="943_EDI",
                       remote_name=built.file_name, size=len(built.content), sha256=hashlib.sha256(built.content).hexdigest(),
                       content=built.content, file_status=OutboundFile.READY_TO_SEND.value, ack_status=Ack.WAITING.value,
                       isa13=built.control, gs06=built.control, st02=built.control,
                       body_json={"transfer_id": t.id, "errors": built.errors + built.issues.errors,
                                  "transliterated": built.issues.changed, "reason": reason})
        s.add(doc)
        await s.flush()
        s.add(DocumentLink(document_id=doc.id, entity_type="transfer", entity_id=t.id))
        t.document_id, t.error = doc.id, None
        TRANSFER.transition(t, T.GENERATED_943, actor=uow.actor, events=uow.events, reason=reason)
        if built.ok:
            OUTBOX.add(s, SEND_943, {"document_id": doc.id, "transfer_id": t.id}, entity=t)
        else:
            problems = built.errors + built.issues.errors
            doc.error = t.error = "; ".join(problems)
            OUTBOUND_FILE.transition(doc, OutboundFile.CANCELLED, actor=uow.actor, events=uow.events, reason="failed its checks")
            await self._fail(uow, t, f"The 943 for transfer {t.transfer_number} failed its checks and was not sent: "
                             + "; ".join(problems) + ". Correct the warehouses or the Item Master, then Resend.")
        await s.flush()
        return doc

    async def _fail(self, uow, t: Transfer, description: str, *, task: bool = True) -> None:
        if t.status != T.FAILED.value:
            TRANSFER.transition(t, T.FAILED, actor=uow.actor, events=uow.events, reason=description[:200])
        if task:
            await self.tasks.raise_problem(uow.session, TRANSFER_FAILED, t.id, problem(
                TRANSFER_FAILED, f"Transfer {t.transfer_number}: 943 not sent", description))

    async def queue_ready(self, *, run_id: int | None = None) -> TransferReport:
        report = TransferReport()
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            if await SETTINGS.get(uow.session, SHADOW_MODE):
                report.skipped = "shadow run: no 943 is generated"
                return report
            if await self._connection(uow.session) is None:
                report.skipped = "no warehouse connection set"
                return report
            ids = list((await uow.session.execute(select(Transfer.id).where(Transfer.status == T.READY_TO_SEND.value)
                                                  .order_by(Transfer.id))).scalars())
        for tid in ids:
            async def one(uow, tid=tid):
                t = await uow.session.get(Transfer, tid, with_for_update=True)
                if t.status != T.READY_TO_SEND.value:
                    return "skip"
                doc = await self.generate(uow, t, reason="ready to send (FR-OB-80)")
                return "generated" if doc.file_status == OutboundFile.READY_TO_SEND.value else "invalid"
            out = await isolated(f"943 transfer {tid}", one, uow_factory=self.uow_factory, log=self._log_failure(run_id))
            if out.ok and out.result in ("generated", "invalid"):
                setattr(report, out.result, getattr(report, out.result) + 1)
            elif not out.ok:
                report.failed += 1
        return report

    def _log_failure(self, run_id):
        async def log(o) -> None:
            await oplog.write_alone(self.uow_factory, "ERROR", STEP, f"{o.unit}: {o.error_type}: {o.message}", run_id=run_id)
        return log

    def outbox_kind(self) -> OutboxKind:
        return OutboxKind(effect=self._effect, policy=ONCE, pre_check=self._pre_check, on_done=self._done,
                          on_failed=self._failed, on_suppressed=self._suppressed)

    async def _pair(self, payload, uow):
        s = uow.session
        return (await s.get(Document, payload["document_id"], with_for_update=True),
                await s.get(Transfer, payload["transfer_id"], with_for_update=True))

    async def _pre_check(self, payload: dict[str, Any], uow) -> str | None:
        doc, t = await self._pair(payload, uow)
        if doc is None or t is None:
            return "the document or the transfer is gone"
        if await SETTINGS.get(uow.session, SHADOW_MODE):  # the second barrier
            await oplog.write(uow.session, "WARNING", STEP, f"shadow run: 943 {doc.st02} not sent (outbox row cancelled)",
                              document_id=doc.id)
            if t.status == T.GENERATED_943.value:
                t.error = "not sent: the shadow run"
                await self._fail(uow, t, "", task=False)
            return "the shadow run is on: nothing is sent"
        if doc.file_status != OutboundFile.READY_TO_SEND.value:
            return f"the 943 is {doc.file_status}"
        if t.status != T.GENERATED_943.value:
            return f"the transfer is {t.status}"
        OUTBOUND_FILE.transition(doc, OutboundFile.SENDING, actor=uow.actor, events=uow.events, reason="sending the 943")
        return None

    async def _effect(self, payload: dict[str, Any], ctx) -> None:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            doc = await uow.session.get(Document, payload["document_id"])
            content = (await uow.session.execute(select(Document.content).where(Document.id == doc.id))).scalar_one()
            conn = ConnectionSnapshot.of(await uow.session.get(Connection, doc.connection_id))
            name = doc.remote_name
        folder = conn.test_outbound_folder if conn.mode == "TEST" else conn.outbound_folder
        if not folder:
            raise RuntimeError(f"connection {conn.name} has no {'test ' if conn.mode == 'TEST' else ''}outbound folder")
        await self.sftp.put(conn, folder, name, content)  # guarded: switch + LIVE + write host

    async def _done(self, row, exc, uow) -> None:
        doc, t = await self._pair(row.payload, uow)
        OUTBOUND_FILE.transition(doc, OutboundFile.SENT, actor=uow.actor, events=uow.events, reason="943 put")
        doc.sent_at = self.clock()
        TRANSFER.transition(t, T.WAITING_FOR_997, actor=uow.actor, events=uow.events, reason="943 sent; waiting for the 997")
        await self.tasks.resolve_problem(uow.session, TRANSFER_FAILED, t.id)

    async def _failed(self, row, exc, uow) -> None:
        doc, t = await self._pair(row.payload, uow)
        if doc.file_status == OutboundFile.SENDING.value:
            OUTBOUND_FILE.transition(doc, OutboundFile.FAILED, actor=uow.actor, events=uow.events, reason=str(exc)[:200])
        doc.error = t.error = str(exc)[:2000]
        await self._fail(uow, t, f"The 943 for transfer {t.transfer_number} could not be sent: {exc}. "
                         "It is not retried automatically — check, then Resend.")

    async def _suppressed(self, row, exc, uow) -> None:
        doc, t = await self._pair(row.payload, uow)
        if doc.file_status in (OutboundFile.READY_TO_SEND.value, OutboundFile.SENDING.value):
            OUTBOUND_FILE.transition(doc, OutboundFile.SUPPRESSED, actor=uow.actor, events=uow.events, reason=str(exc)[:200])
        t.error = f"not sent: {exc}"[:2000]
        await self._fail(uow, t, "", task=False)
        await oplog.write(uow.session, "INFO", STEP, f"943 {doc.remote_name} refused by the guard: {exc}", document_id=doc.id)
        await send_refused(self.tasks, uow.session, row, f"the 943 for transfer {t.transfer_number}", exc)

    # ── the 997 (called by the 997 step for an AK2 943, or a group-level ack) ─
    async def apply_997(self, uow, ack_doc: Document, g, st) -> int:
        """`st` None: a group-level ack (no AK2, Radiant's real 997s) — the 943
        with that GS06, answered by AK9."""
        s = uow.session
        docs = list((await s.execute(select(Document).where(
            Document.kind == "943_EDI", Document.direction == "OUT", Document.origin == "UNIOPS",
            func.ltrim(Document.gs06, "0") == g.group_control.lstrip("0"),
            *([func.ltrim(Document.st02, "0") == st.control.lstrip("0")] if st is not None else [])).with_for_update())).scalars())
        if len(docs) != 1:
            return 0
        doc = docs[0]
        t = (await s.execute(select(Transfer).join(DocumentLink, (DocumentLink.entity_id == Transfer.id) &
                                                   (DocumentLink.entity_type == "transfer"))
                             .where(DocumentLink.document_id == doc.id).with_for_update())).scalar_one()
        codes = (" ".join(x for x in [st.code or "", *st.reasons] if x) if st is not None
                 else f"- / AK9 {g.code or '-'} (group level)")
        doc.ack_detail = f"997 {ack_doc.remote_name}: AK5 {codes}".strip()
        if (st is None or st.accepted) and g.accepted:
            if doc.ack_status in (Ack.WAITING.value, Ack.TIMEOUT.value):
                ACK.transition(doc, Ack.ACCEPTED, actor=uow.actor, events=uow.events, reason="997 accepted")
            if t.status == T.WAITING_FOR_997.value:
                TRANSFER.transition(t, T.WAITING_FOR_944, actor=uow.actor, events=uow.events, reason="997 accepted (FR-OB-82)")
            await self.tasks.resolve_problem(s, EDI_943_TIMEOUT, t.id)
        else:
            if doc.ack_status in (Ack.WAITING.value, Ack.TIMEOUT.value):
                ACK.transition(doc, Ack.REJECTED, actor=uow.actor, events=uow.events, reason=f"997 rejected: AK5 {codes}")
            if t.status == T.WAITING_FOR_997.value:
                TRANSFER.transition(t, T.FAILED, actor=uow.actor, events=uow.events, reason=f"997 rejected: AK5 {codes}")
            await self.tasks.raise_problem(s, EDI_943_REJECTED, t.id, problem(
                EDI_943_REJECTED, f"943 rejected: transfer {t.transfer_number}",
                f"The warehouse rejected the 943 for transfer {t.transfer_number} (control {doc.st02}): AK5 {codes}. "
                "It is not resent automatically — correct it, then Resend, or cancel the transfer."))
        return 1

    async def timeouts(self, *, run_id: int | None = None) -> TransferReport:
        report = TransferReport()
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            s = uow.session
            minutes = await SETTINGS.get(s, ACK_TIMEOUT_MINUTES)
            due = self.clock() - timedelta(minutes=minutes)
            docs = list((await s.execute(select(Document).where(
                Document.kind == "943_EDI", Document.direction == "OUT", Document.file_status == OutboundFile.SENT.value,
                Document.ack_status == Ack.WAITING.value, Document.sent_at <= due).with_for_update(skip_locked=True))).scalars())
            for doc in docs:
                ACK.transition(doc, Ack.TIMEOUT, actor=uow.actor, events=uow.events, reason=f"no 997 within {minutes} minutes")
                t = await s.get(Transfer, (doc.body_json or {}).get("transfer_id"))
                if t is not None:
                    await self.tasks.raise_problem(s, EDI_943_TIMEOUT, t.id, problem(
                        EDI_943_TIMEOUT, f"No 997 for transfer {t.transfer_number}",
                        f"No 997 arrived within {minutes} minutes for the 943 of transfer {t.transfer_number} (control "
                        f"{doc.st02}, sent {doc.sent_at.astimezone(TORONTO):%Y-%m-%d %H:%M} Toronto time). The transfer "
                        "waits; check with the warehouse."))
                report.timeouts += 1
            await uow.commit()
        return report

    # ── the 944 ──────────────────────────────────────────────────────────────
    async def process_944s(self, *, run_id: int | None = None, connection_id: int | None = None) -> TransferReport:
        report = TransferReport()
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            ids = list((await uow.session.execute(select(Document.id).where(
                Document.kind == "944_EDI", Document.direction == "IN",
                Document.processing_status == InboundFile.PENDING.value,
                *([Document.connection_id == connection_id] if connection_id is not None else []))
                .order_by(Document.id))).scalars())
        for did in ids:
            out = await isolated(f"944 document {did}", lambda uow, did=did: self._one(uow, did, report, run_id),
                                 uow_factory=self.uow_factory, log=self._log_failure(run_id))
            if not out.ok:
                report.failed += 1
                report.detail.append(f"document {did}: {out.message}")
        return report

    async def _one(self, uow, did: int, report: TransferReport, run_id) -> str:
        s = uow.session
        doc = await s.get(Document, did, with_for_update=True)
        INBOUND_FILE.transition(doc, InboundFile.PROCESSING, actor=uow.actor, events=uow.events, reason="reading the 944")
        content = (await s.execute(select(Document.content).where(Document.id == did))).scalar_one()
        try:
            r = parse_944(content)
        except X12Error as e:
            doc.error = str(e)
            INBOUND_FILE.transition(doc, InboundFile.FAILED, actor=uow.actor, events=uow.events, reason=str(e))
            await self.tasks.raise_problem(s, ORDER_FILE_UNREADABLE, doc.id, problem(  # raised in the shadow run too
                ORDER_FILE_UNREADABLE, f"944 could not be read: {doc.remote_name}"[:255],
                f"944 file {doc.remote_name} (document {doc.id}) was stored but cannot be read: {e}."))
            return "failed"
        shadow = doc.shadow or bool(await SETTINGS.get(s, SHADOW_MODE))
        t = (await s.execute(select(Transfer).where(Transfer.transfer_number.in_(
            [r.transfer_key, r.transfer_key.zfill(5)])).with_for_update())).scalars().first()
        earlier = []
        if t is not None:
            earlier = (await s.execute(select(Document.remote_name).join(Receipt, Receipt.document_id == Document.id).where(
                Receipt.transfer_id == t.id, Receipt.applied.is_(True), Receipt.document_id != doc.id))).scalars().all()
        lines = list((await s.execute(select(TransferLine).where(TransferLine.transfer_id == t.id)
                                      .order_by(TransferLine.line_no).with_for_update())).scalars()) if t else []
        a = allocate([LineIn(ln.line_no, ln.item_code, ln.qty_ea) for ln in lines], r.rows, r.total)
        applied = t is not None and not earlier and not shadow
        rec = Receipt(document_id=doc.id, transfer_id=t.id if t else None, transfer_key_received=r.transfer_key[:20],
                      receipt_date=r.receipt_date, total_ea=r.total, applied=applied, shadow=shadow,
                      outcome=a.outcome if t is not None else None, discrepancies=a.discrepancies or None)
        s.add(rec)
        await s.flush()
        for i, (row, line_no) in enumerate(zip(r.rows, a.row_line), start=1):
            s.add(ReceiptRow(receipt_id=rec.id, position=i, lx_number=row.lx_number, item_code=row.item_code, upc=row.upc,
                             lot=row.lot, description=row.description, qty_ea=row.qty, transfer_line_no=line_no))
        if t is not None:
            s.add(DocumentLink(document_id=doc.id, entity_type="transfer", entity_id=t.id))
        await s.flush()
        if shadow:
            doc.shadow = True
            if self.shadow is not None:
                await self.shadow.match_944(uow, doc, r, t, a)
            doc.error = "shadow run: stored and compared, not applied"
            INBOUND_FILE.transition(doc, InboundFile.SKIPPED, actor=uow.actor, events=uow.events, reason="shadow run")
            await oplog.write(s, "INFO", STEP, f"shadow run: 944 {doc.remote_name} compared, not applied", document_id=doc.id)
            report.shadow += 1
            return "shadow"
        INBOUND_FILE.transition(doc, InboundFile.PROCESSED, actor=uow.actor, events=uow.events, reason="944 applied")
        if t is None:
            report.no_transfer += 1
            await self.tasks.raise_problem(s, EDI_944_NO_TRANSFER, rec.id, problem(
                EDI_944_NO_TRANSFER, f"944 matches no transfer: {r.transfer_key}"[:255],
                f"944 file {doc.remote_name}: transfer {r.transfer_key} is not in Sales. It is stored with its "
                f"{len(r.rows)} line(s) (receipt {rec.id}); find the transfer or tell the warehouse."))
            return "no_transfer"
        if earlier:
            report.second += 1
            if T(t.status) in TRANSFER.holds[T.ON_HOLD]:
                TRANSFER.transition(t, T.ON_HOLD, actor=uow.actor, events=uow.events, reason="a second, different 944 (FR-OB-83)")
            await self.tasks.raise_problem(s, SECOND_944, t.id, problem(
                SECOND_944, f"Second 944 for transfer {t.transfer_number}",
                f"Transfer {t.transfer_number} was already received by {', '.join(x or '?' for x in earlier)}; "
                f"{doc.remote_name} differs and was stored without changing any quantity. The transfer is On Hold: "
                "compare both 944s and decide.", urgent=True))
            return "second"
        sent = await s.get(Document, t.document_id, with_for_update=True) if t.document_id else None
        if t.status == T.WAITING_FOR_997.value:  # the 944 is the answer: the 943 was taken
            if sent is not None and sent.ack_status in (Ack.WAITING.value, Ack.TIMEOUT.value):
                ACK.transition(sent, Ack.ACCEPTED, actor=uow.actor, events=uow.events, reason=f"944 {doc.remote_name} arrived first")
                sent.ack_detail = f"accepted by 944 {doc.remote_name}"
            TRANSFER.transition(t, T.WAITING_FOR_944, actor=uow.actor, events=uow.events, reason="944 before the 997")
        for ln in lines:
            ln.received_ea = a.received.get(ln.line_no, 0)
            ln.line_status = a.line_status.get(ln.line_no, "Open")
        if t.status == T.WAITING_FOR_944.value and a.outcome != "Waiting for 944":
            TRANSFER.transition(t, T(a.outcome), actor=uow.actor, events=uow.events, reason=f"944 {doc.remote_name} (FR-OB-84)")
        report.receipts += 1
        if a.outcome == "Discrepancy":
            await self.tasks.raise_problem(s, TRANSFER_DISCREPANCY, t.id, problem(
                TRANSFER_DISCREPANCY, f"Transfer {t.transfer_number}: receipt differs",
                f"944 {doc.remote_name} for transfer {t.transfer_number}:\n" + "\n".join(a.discrepancies)))
        elif a.outcome == "Partially Received":
            received = "; ".join(f"line {k}: {v} EA" for k, v in a.received.items())
            await oplog.write(s, "WARNING", STEP, f"transfer {t.transfer_number} partially received: {received}",
                              document_id=doc.id)
            await self.tasks.raise_problem(s, PARTIAL_RECEIPT, t.id, problem(  # FR-OB-84: notifies
                PARTIAL_RECEIPT, f"Transfer {t.transfer_number}: partially received",
                f"944 {doc.remote_name} for transfer {t.transfer_number} received part of what was sent: {received}. "
                "The rest may follow in a later 944."))
        await self.tasks.resolve_problem(s, EDI_943_TIMEOUT, t.id)
        await s.flush()
        return "applied"
