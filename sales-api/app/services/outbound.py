"""940 / 997 (P4; FR-OB-03c/d, 16, 20, 22, 22a, 23, 24, 29a; Appendix A).

- Eligibility: `eligible_clause()` is the SQL twin of the pure
  `order_rules.qualifies()` — a test keeps the two equal; every candidate is
  locked and checked once more with `qualifies()` before anything is written.
- Generation: one 940 per order, its control number from the shared sequence
  (FR-OB-22), validated (FR-OB-29a). A file that fails validation is not sent:
  the order goes 940 Generated → 940 Failed and a task names the errors.
- Sending: an outbox row (ONCE). `pre_check` re-reads document and order under
  lock; the effect puts the file in the warehouse connection's outbound folder
  (TEST mode: its test folder) through the guarded gateway, so a write needs
  the switch, a LIVE connection and a write host (§5.5) — else Suppressed.
  Failure is never retried by itself (FR-OB-24).
- 997s: matched by AK1 + AK2 (leading zeros normalised), the warehouse and the
  group / document type (FR-OB-22a); A / E accepted, anything else rejected
  with its AK5 codes on the task. No match or several: review.
- Timeout (FR-OB-23) and the late answers the state machine allows.
- Send 940 now (FR-OB-03c) and the source change after a 940 (FR-OB-16).
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.sql.elements import ColumnElement

from app.domain.edi940 import Address, Line940, Order940, build_940
from app.domain.edi997 import parse_997
from app.domain.order_rules import facts_of, qualifies
from app.domain.orders import PAYMENT_SATISFIED
from app.domain.status import ACK, FULFILLMENT, INBOUND_FILE, OUTBOUND_FILE, Ack, InboundFile, OutboundFile
from app.domain.status import Fulfillment as F
from app.domain.status import Invoice as Inv
from app.domain.x12 import X12Error
from app.models.config import Connection, DcRouting, Item, Partner
from app.models.documents import Document, DocumentLink
from app.models.orders import Order, OrderLine
from app.models.records import OutboxRecord
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.outbox import Outbox, OutboxKind
from app.platform.pipeline import isolated
from app.services import oplog
from app.services.intake import ConnectionSnapshot
from app.services.problems import (
    CHANGED_AFTER_940, EDI_940_FAILED, EDI_940_REJECTED, EDI_940_TIMEOUT, EDI_997_UNMATCHED, problem,
)
from app.services.settings import ACK_TIMEOUT_MINUTES, EDI_CONNECTION_ID, SETTINGS, SHADOW_MODE

logger = logging.getLogger(__name__)
TORONTO = ZoneInfo("America/Toronto")  # "today" on a 940 (product owner, 2026-10-30)
SEND_940 = "send_940"
STEP = "edi"
LIVE_940_FILE = (OutboundFile.READY_TO_SEND, OutboundFile.SENDING, OutboundFile.SENT)
OUTBOX = Outbox(OutboxRecord)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── eligibility: the SQL twin of qualifies() ─────────────────────────────────
def eligible_clause(now: datetime) -> ColumnElement[bool]:
    """FR-OB-03d in SQL, over Order joined to its Partner — the same
    conditions as order_rules.qualifies(), in the same order."""
    o, p = Order, Partner
    started = (p.settlement_mode == "INVOICE") & (o.first_received_at <= now - p.delay_minutes * text("interval '1 minute'"))
    paid_started = (p.settlement_mode != "INVOICE") & o.paid_at.is_not(None) & (
        o.paid_at <= now - p.delay_minutes * text("interval '1 minute'"))
    return and_(
        # order state Active: with fulfillment Pending, only an invoice Blocked / Failed makes it On Hold
        o.fulfillment_status == F.PENDING.value,
        o.invoice_status.not_in((Inv.BLOCKED.value, Inv.FAILED.value)),
        or_(p.settlement_mode == "INVOICE", o.source_type == "MANUAL", o.shopify_financial_status.in_(PAYMENT_SATISFIED)),
        or_(o.shopify_fulfillment_status.is_(None), o.shopify_fulfillment_status != "fulfilled"),
        o.archived_in_shopify_at.is_(None),
        o.historical.is_(False),
        o.shadow.is_(False),
        p.sending_mode == "AUTOMATIC",
        or_(started, paid_started),
    )


@dataclass
class EdiReport:
    queued: int = 0
    invalid: int = 0
    skipped: str | None = None
    acks: int = 0
    unmatched_997: int = 0
    shadow_997: int = 0  # stored only during the shadow run (§5.4)
    timeouts: int = 0
    failed: int = 0
    detail: list[str] = field(default_factory=list)


def file_name(abbrev: str | None, order: Order, at: datetime) -> str:
    """A.10: 940-<partner abbrev>-<internal number>-<source number>-<yyyyMMdd_HHmmss>.edi"""
    safe = re.sub(r"[^A-Za-z0-9._-]", "", str(order.source_order_number or ""))
    return f"940-{abbrev or order.partner_code}-{order.order_number:09d}-{safe}-{at:%Y%m%d_%H%M%S}.edi"


class Outbound940:
    def __init__(self, *, uow_factory, tasks, sftp, clock=now_utc, shadow=None) -> None:
        self.uow_factory = uow_factory
        self.tasks = tasks
        self.sftp = sftp
        self.clock = clock
        self.shadow = shadow  # the shadow-run matcher (ShadowRun), set by the runtime
        self.transfers = None  # P7: the 943's 997s go to Transfers.apply_997 (set by the runtime)

    # ── the outbox kind ──────────────────────────────────────────────────────
    def outbox_kind(self) -> OutboxKind:
        return OutboxKind(self._effect, pre_check=self._pre_check, on_done=self._on_done,
                          on_failed=self._on_failed, on_suppressed=self._on_suppressed)

    async def _pre_check(self, payload: dict[str, Any], uow) -> str | None:
        doc = await uow.session.get(Document, payload["document_id"], with_for_update=True)
        order = await uow.session.get(Order, payload["order_id"], with_for_update=True)
        if doc is None or order is None:
            return "the document or the order is gone"
        if await SETTINGS.get(uow.session, SHADOW_MODE):  # second barrier: a row queued before the switch-on
            await oplog.write(uow.session, "WARNING", STEP, f"shadow run: 940 {doc.st02} for order {order.order_number} "
                              "not sent (outbox row cancelled)", order_id=order.id, document_id=doc.id)
            return "the shadow run is on: nothing is sent"
        if doc.file_status not in (OutboundFile.READY_TO_SEND, OutboundFile.SUPPRESSED):
            return f"the 940 is {doc.file_status}"
        if order.fulfillment_status != F.GENERATED_940:
            return f"the order is {order.fulfillment_status}"  # e.g. cancelled at source meanwhile (FR-OB-18)
        OUTBOUND_FILE.transition(doc, OutboundFile.SENDING, actor=uow.actor, events=uow.events, reason="sending the 940")
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

    async def _on_done(self, row, exc, uow) -> None:
        doc, order = await self._pair(row, uow)
        OUTBOUND_FILE.transition(doc, OutboundFile.SENT, actor=uow.actor, events=uow.events, reason="940 put")
        doc.sent_at = self.clock()
        FULFILLMENT.transition(order, F.SENT_940, actor=uow.actor, events=uow.events, reason="940 sent; waiting for the 997")
        await self.tasks.resolve_problem(uow.session, EDI_940_FAILED, order.id)

    async def _on_failed(self, row, exc, uow) -> None:
        doc, order = await self._pair(row, uow)
        if doc.file_status in (OutboundFile.SENDING,):
            OUTBOUND_FILE.transition(doc, OutboundFile.FAILED, actor=uow.actor, events=uow.events, reason=str(exc)[:200])
        doc.error = str(exc)
        if order.fulfillment_status == F.GENERATED_940:
            FULFILLMENT.transition(order, F.FAILED_940, actor=uow.actor, events=uow.events, reason="940 not sent (FR-OB-24)")
        await self._task(uow, EDI_940_FAILED, order, f"940 not sent: order {order.order_number}",
                         f"The 940 for order {order.order_number} ({order.partner_code} {order.source_order_number}) "
                         f"could not be sent: {exc}. It is not retried automatically — check, then use Send 940 now.")

    async def _on_suppressed(self, row, exc, uow) -> None:
        doc, order = await self._pair(row, uow)
        if doc.file_status == OutboundFile.SENDING:
            OUTBOUND_FILE.transition(doc, OutboundFile.SUPPRESSED, actor=uow.actor, events=uow.events,
                                     reason=f"refused by the write guard: {exc}")
        await oplog.write(uow.session, "WARNING", STEP, f"940 for order {order.order_number} suppressed: {exc}",
                          document_id=doc.id, order_id=order.id)

    async def _pair(self, row, uow) -> tuple[Document, Order]:
        p = row.payload
        return (await uow.session.get(Document, p["document_id"], with_for_update=True),
                await uow.session.get(Order, p["order_id"], with_for_update=True))

    async def _task(self, uow, event: str, order: Order, title: str, text_: str, *, urgent: bool = True) -> None:
        await self.tasks.raise_problem(uow.session, event, order.id, problem(event, title, text_, urgent=urgent))

    # ── generation ───────────────────────────────────────────────────────────
    async def _connection(self, session) -> Connection | None:
        cid = await SETTINGS.get(session, EDI_CONNECTION_ID)
        conn = await session.get(Connection, cid) if cid else None
        return conn if conn is not None and conn.active and conn.kind == "SFTP" else None

    async def snapshot(self, session, order: Order, partner: Partner) -> Order940:
        lines = list((await session.execute(select(OrderLine).where(
            OrderLine.order_id == order.id, OrderLine.removed.is_(False)).order_by(OrderLine.line_no))).scalars())
        codes = {ln.item_code for ln in lines if ln.item_code}
        items = {i.item_code: i for i in (await session.execute(select(Item).where(Item.item_code.in_(codes)))).scalars()}
        retail = order.source_type == "EDI_850"
        gln = (order.ship_to or {}).get("gln")
        dc = None
        if retail and gln:
            dc = (await session.execute(select(DcRouting.dc_number).where(
                DcRouting.partner_code == order.partner_code, DcRouting.ship_to_gln == str(gln).strip(),
                DcRouting.active.is_(True)))).scalar_one_or_none()
        out = []
        for ln in lines:
            if not ln.item_code or not ln.qty_ea:
                continue
            item = items.get(ln.item_code)
            src = ln.source or {}
            out.append(Line940(
                line_no=ln.line_no, item_code=ln.item_code, qty_ea=int(ln.qty_ea), buyer_part=src.get("buyer_part"),
                gtin=(item.gtin if item else None) or src.get("gtin"), item_name=item.description if item else None,
                weight_lb_per_ea=Decimal(item.gross_weight_lb_per_ea) if item and item.gross_weight_lb_per_ea is not None else None,
                warehouse_held=bool(item and item.warehouse_held)))
        return Order940(
            order_id=order.id, order_number=order.order_number, retail=retail,
            source_order_number=str(order.source_order_number or ""), source_reference=order.source_reference,
            partner_code=order.partner_code, walmart=bool(partner.dc_routing_required), label_code=partner.label_code,
            dc_number=dc, order_type=order.order_type, department=order.department,
            requested_delivery=order.requested_delivery_date, ship_to=Address.of(order.ship_to),
            bill_to=Address.of(order.bill_to), contact=order.contact or {},
            express="express" in (order.shipping_method or "").lower(), lines=tuple(out))

    async def generate(self, uow, order: Order, partner: Partner, *, reason: str) -> Document:
        """One 940 for a locked order in Pending / a resend status: the file,
        its record (locked values) and, when valid, the outbox row."""
        s = uow.session
        if await SETTINGS.get(s, SHADOW_MODE):
            raise BusinessError("send940.shadow", "the shadow run is on: Sales sends no 940 (Zoho is live)")
        if order.shadow:
            raise BusinessError("send940.shadow_order", "this order was received during the shadow run: Zoho handles it")
        conn = await self._connection(s)
        if conn is None:
            raise BusinessError("edi.no_connection", "no warehouse connection is set for 940s (setting "
                                f"{EDI_CONNECTION_ID.key}); nothing was generated")
        snap = await self.snapshot(s, order, partner)
        ctl = (await s.execute(text("SELECT nextval('sales_x12_control_seq')"))).scalar_one()
        now = self.clock()
        built = build_940(snap, ctl, now.astimezone(TORONTO))
        doc = Document(
            direction="OUT", origin="UNIOPS", system="RADIANT", connection_id=conn.id, kind="940_EDI",
            remote_name=file_name(partner.abbrev, order, now.astimezone(TORONTO)), size=len(built.content),
            sha256=hashlib.sha256(built.content).hexdigest(), content=built.content,
            body_json={"order_id": order.id, "lines": built.lines_sent, "locked": built.locked,
                       "transliterated": built.issues.changed, "errors": built.errors + built.issues.errors,
                       "reason": reason},
            file_status=OutboundFile.READY_TO_SEND.value, ack_status=Ack.WAITING.value,
            isa13=built.control, gs06=built.control, st02=built.control, partner_code=order.partner_code)
        s.add(doc)
        await s.flush()
        s.add(DocumentLink(document_id=doc.id, entity_type="order", entity_id=order.id))
        FULFILLMENT.transition(order, F.GENERATED_940, actor=uow.actor, events=uow.events, reason=reason)
        if built.ok:
            OUTBOX.add(s, SEND_940, {"document_id": doc.id, "order_id": order.id}, entity=order)
        else:
            problems = built.errors + built.issues.errors
            doc.error = "; ".join(problems)
            OUTBOUND_FILE.transition(doc, OutboundFile.CANCELLED, actor=uow.actor, events=uow.events,
                                     reason="failed validation (FR-OB-29a)")
            FULFILLMENT.transition(order, F.FAILED_940, actor=uow.actor, events=uow.events,
                                   reason="940 failed validation (FR-OB-29a)")
            await self._task(uow, EDI_940_FAILED, order, f"940 not valid: order {order.order_number}",
                             f"The 940 for order {order.order_number} ({order.partner_code} {order.source_order_number}) "
                             "failed validation and was not sent: " + "; ".join(problems)
                             + ". Correct the order or the Item Master, then use Send 940 now.")
        await s.flush()
        return doc

    # ── the cycle's step ─────────────────────────────────────────────────────
    async def queue_due(self, *, run_id: int | None = None, limit: int = 100) -> EdiReport:
        """Generate the 940s of every order that qualifies now."""
        report = EdiReport()
        now = self.clock()
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            if await SETTINGS.get(uow.session, SHADOW_MODE):
                report.skipped = "shadow run: no 940 is generated"
                return report
            if await self._connection(uow.session) is None:
                report.skipped = "no warehouse connection set"
                return report
            ids = list((await uow.session.execute(
                select(Order.id).join(Partner, Partner.code == Order.partner_code)
                .where(eligible_clause(now)).order_by(Order.first_received_at, Order.id).limit(limit))).scalars())
        for oid in ids:
            async def one(uow, oid=oid):
                order = await uow.session.get(Order, oid, with_for_update=True)
                partner = (await uow.session.execute(select(Partner).where(Partner.code == order.partner_code))).scalar_one()
                ok, why = qualifies(*facts_of(order, partner), self.clock())
                if not ok:  # changed since the query: nothing to do
                    return "skip"
                doc = await self.generate(uow, order, partner, reason="eligible (FR-OB-03d)")
                return "queued" if doc.file_status == OutboundFile.READY_TO_SEND else "invalid"
            outcome = await isolated(f"940 order {oid}", one, uow_factory=self.uow_factory, log=self._log_failure(run_id))
            if outcome.ok and outcome.result in ("queued", "invalid"):
                setattr(report, outcome.result, getattr(report, outcome.result) + 1)
            elif not outcome.ok:
                report.failed += 1
                report.detail.append(f"order {oid}: {outcome.message}")
        return report

    async def preview(self, session, *, limit: int = 1000) -> list[dict[str, Any]]:
        """AC-OB-21: the orders that would be queued now (a dry run; writes nothing)."""
        now = self.clock()
        rows = (await session.execute(
            select(Order, Partner).join(Partner, Partner.code == Order.partner_code)
            .where(eligible_clause(now)).order_by(Order.first_received_at, Order.id).limit(limit))).all()
        return [{"order_id": o.id, "order_number": o.order_number, "partner_code": o.partner_code,
                 "source_order_number": o.source_order_number, "historical": o.historical,
                 "qualifies": qualifies(*facts_of(o, p), now)[0]} for o, p in rows]

    def _log_failure(self, run_id: int | None):
        async def log(outcome) -> None:
            await oplog.write_alone(self.uow_factory, "ERROR", STEP, f"{outcome.unit}: {outcome.error_type}: {outcome.message}",
                                    run_id=run_id)
        return log

    # ── Send 940 now (FR-OB-03c) ─────────────────────────────────────────────
    async def send_now(self, uow, order_id: int, *, replace: bool = False) -> Document:
        s = uow.session
        order = await s.get(Order, order_id, with_for_update=True)
        if order is None:
            raise BusinessError("order.not_found", "no such order", status_code=404)
        partner = (await s.execute(select(Partner).where(Partner.code == order.partner_code))).scalar_one()
        if order.historical:
            raise BusinessError("send940.historical", "a historical order is never sent (FR-OB-17)")
        if await SETTINGS.get(s, SHADOW_MODE):
            raise BusinessError("send940.shadow", "the shadow run is on: Sales sends no 940 (Zoho is live)")
        if order.shadow:
            raise BusinessError("send940.shadow_order", "this order was received during the shadow run: Zoho handles it")
        status = F(order.fulfillment_status)
        live = await self.live_940(s, order.id)
        if status == F.GENERATED_940:
            raise BusinessError("send940.in_progress", "a 940 for this order is being sent")
        if live is not None and not replace:
            raise BusinessError("send940.live", "a 940 for this order is live; resend only as a confirmed replacement",
                                {"document_id": live.id, "needs": "replace"})
        if replace:
            if live is None or status not in (F.SENT_940, F.ACCEPTED_940):
                raise BusinessError("send940.nothing_to_replace", "there is no live 940 to replace")
            OUTBOUND_FILE.transition(live, OutboundFile.REPLACED, actor=uow.actor, events=uow.events,
                                     reason="replaced by a confirmed resend (FR-OB-03c)")
            await self.tasks.resolve_problem(s, CHANGED_AFTER_940, order.id, by=uow.actor.user_id)
        elif status == F.PENDING:
            o, p = facts_of(order, partner)
            ok, why = qualifies(o, type(p)(p.settlement_mode, "AUTOMATIC", 0), self.clock())  # now: no delay, any mode
            if not ok:
                raise BusinessError("send940.not_eligible", f"the order cannot be sent: {why}")
        elif status not in (F.REJECTED_940, F.TIMEOUT_940, F.FAILED_940):
            raise BusinessError("send940.not_allowed", f"a 940 cannot be sent for an order in {status}")
        doc = await self.generate(uow, order, partner, reason="replacement resend (FR-OB-03c)" if replace else "Send 940 now (FR-OB-03c)")
        if replace:
            live.replaced_by_id = doc.id
        for event in (EDI_940_REJECTED, EDI_940_TIMEOUT):
            await self.tasks.resolve_problem(s, event, order.id, by=uow.actor.user_id)
        return doc

    async def live_940(self, session, order_id: int) -> Document | None:
        """FR-OB-03c: sent (or on its way) and not rejected, failed, timed out,
        cancelled or replaced."""
        q = (select(Document).join(DocumentLink, and_(DocumentLink.document_id == Document.id,
                                                      DocumentLink.entity_type == "order", DocumentLink.entity_id == order_id))
             .where(Document.kind == "940_EDI", Document.direction == "OUT", Document.file_status.in_(LIVE_940_FILE),
                    Document.ack_status.not_in((Ack.REJECTED.value, Ack.TIMEOUT.value)))
             .order_by(Document.id.desc()).limit(1).with_for_update())
        return (await session.execute(q)).scalar_one_or_none()

    # ── 997s (FR-OB-22a) ─────────────────────────────────────────────────────
    async def process_997s(self, *, run_id: int | None = None) -> EdiReport:
        report = EdiReport()
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            ids = list((await uow.session.execute(select(Document.id).where(
                Document.kind == "997_EDI", Document.direction == "IN",
                Document.processing_status == InboundFile.PENDING.value).order_by(Document.id))).scalars())
        for did in ids:
            async def one(uow, did=did):
                doc = await uow.session.get(Document, did, with_for_update=True)
                INBOUND_FILE.transition(doc, InboundFile.PROCESSING, actor=uow.actor, events=uow.events, reason="reading the 997")
                if doc.shadow or await SETTINGS.get(uow.session, SHADOW_MODE):
                    # §5.4: it answers a Zoho 940. Kept and compared with the Zoho references, never applied.
                    doc.shadow = True
                    doc.error = "shadow run: stored and compared with the Zoho references, not applied"
                    if self.shadow is not None:
                        content = (await uow.session.execute(select(Document.content).where(Document.id == did))).scalar_one()
                        try:
                            await self.shadow.match_997(uow, doc, parse_997(content))
                        except X12Error as e:
                            doc.error = f"shadow run: stored, not applied; cannot be read: {e}"
                    INBOUND_FILE.transition(doc, InboundFile.SKIPPED, actor=uow.actor, events=uow.events, reason="shadow run")
                    await oplog.write(uow.session, "INFO", STEP, f"shadow run: 997 {doc.remote_name} stored, not applied",
                                      document_id=doc.id)
                    return "shadow"
                content = (await uow.session.execute(select(Document.content).where(Document.id == did))).scalar_one()
                try:
                    groups = parse_997(content)
                except X12Error as e:
                    doc.error = str(e)
                    INBOUND_FILE.transition(doc, InboundFile.FAILED, actor=uow.actor, events=uow.events, reason=str(e))
                    return "failed"
                matched = 0
                for g in groups:
                    for st in g.sets or []:
                        matched += await self._apply_ack(uow, doc, g, st)
                INBOUND_FILE.transition(doc, InboundFile.PROCESSED, actor=uow.actor, events=uow.events, reason="997 applied")
                return "ack" if matched else "unmatched"
            outcome = await isolated(f"997 document {did}", one, uow_factory=self.uow_factory, log=self._log_failure(run_id))
            if outcome.ok and outcome.result == "ack":
                report.acks += 1
            elif outcome.ok and outcome.result == "unmatched":
                report.unmatched_997 += 1
            elif outcome.ok and outcome.result == "shadow":
                report.shadow_997 += 1
            else:
                report.failed += 1
        return report

    async def _apply_ack(self, uow, ack_doc: Document, g, st) -> int:
        s = uow.session
        if g.functional_id == "OW" and st.type == "943" and self.transfers is not None:
            matched = await self.transfers.apply_997(uow, ack_doc, g, st)  # P7: FR-OB-82
            if not matched:
                await self.tasks.raise_problem(s, EDI_997_UNMATCHED, ack_doc.id, problem(
                    EDI_997_UNMATCHED, "997 matches no 943",
                    f"997 file {ack_doc.remote_name}: AK1 {g.group_control} / AK2 943 {st.control} matches no outbound "
                    "943 (FR-OB-22a). Review it."))
            return matched
        if g.functional_id != "OW" or st.type != "940":
            return 0  # another group
        candidates = list((await s.execute(select(Document).where(
            Document.kind == "940_EDI", Document.direction == "OUT", Document.system == "RADIANT",
            func.ltrim(Document.gs06, "0") == g.group_control.lstrip("0"),
            func.ltrim(Document.st02, "0") == st.control.lstrip("0")).with_for_update())).scalars())
        if len(candidates) != 1:
            await self.tasks.raise_problem(s, EDI_997_UNMATCHED, ack_doc.id, problem(
                EDI_997_UNMATCHED, f"997 matches {'no' if not candidates else 'several'} 940s",
                f"997 file {ack_doc.remote_name}: AK1 {g.group_control} / AK2 {st.control} matches "
                f"{len(candidates)} outbound 940s (FR-OB-22a). Review it."))
            return 0
        doc = candidates[0]
        order = (await s.execute(select(Order).join(DocumentLink, and_(
            DocumentLink.entity_id == Order.id, DocumentLink.entity_type == "order", DocumentLink.document_id == doc.id))
            .with_for_update())).scalar_one()
        s.add(DocumentLink(document_id=ack_doc.id, entity_type="order", entity_id=order.id))
        accepted = st.accepted if st.code else g.accepted
        if doc.ack_status not in (Ack.WAITING, Ack.TIMEOUT):
            await oplog.write(s, "INFO", STEP, f"997 for a 940 already {doc.ack_status}: no change", document_id=doc.id)
            return 1
        codes = f"AK5 {st.code or '-'} {' '.join(st.reasons)} / AK9 {g.code or '-'}".strip()
        doc.ack_detail = f"997 {ack_doc.remote_name}: {codes}"
        await self.tasks.resolve_problem(s, EDI_940_TIMEOUT, order.id)
        if accepted:
            ACK.transition(doc, Ack.ACCEPTED, actor=uow.actor, events=uow.events, reason=f"997 accepted ({codes})")
            if order.fulfillment_status in (F.SENT_940, F.TIMEOUT_940):
                FULFILLMENT.transition(order, F.ACCEPTED_940, actor=uow.actor, events=uow.events, reason="997 accepted")
        else:
            ACK.transition(doc, Ack.REJECTED, actor=uow.actor, events=uow.events, reason=f"997 rejected ({codes})")
            if order.fulfillment_status in (F.SENT_940, F.TIMEOUT_940):
                FULFILLMENT.transition(order, F.REJECTED_940, actor=uow.actor, events=uow.events, reason="997 rejected")
            await self._task(uow, EDI_940_REJECTED, order, f"940 rejected: order {order.order_number}",
                             f"Radiant rejected the 940 for order {order.order_number} ({order.partner_code} "
                             f"{order.source_order_number}), control {doc.st02}: {codes}"
                             f"{' — reason ' + ', '.join(st.reasons) if st.reasons else ''}. "
                             "It is not resent automatically; correct it, then use Send 940 now.")
        return 1

    async def accept_by_945(self, uow, doc: Document, detail: str) -> None:
        """FR-OB-25d / AC-OB-62: a 945 before any accepted 997 accepts the 940
        as "Accepted by 945"; no timeout follows. Called by the 945 step (P5)."""
        if doc.ack_status in (Ack.WAITING, Ack.TIMEOUT):
            ACK.transition(doc, Ack.ACCEPTED_BY_945, actor=uow.actor, events=uow.events, reason=detail)
            doc.ack_detail = detail

    # ── timeout (FR-OB-23) ───────────────────────────────────────────────────
    async def timeouts(self, *, run_id: int | None = None) -> EdiReport:
        report = EdiReport()
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            s = uow.session
            minutes = await SETTINGS.get(s, ACK_TIMEOUT_MINUTES)
            due = self.clock() - timedelta(minutes=minutes)
            docs = list((await s.execute(select(Document).where(
                Document.kind == "940_EDI", Document.direction == "OUT", Document.file_status == OutboundFile.SENT.value,
                Document.ack_status == Ack.WAITING.value, Document.sent_at <= due).with_for_update(skip_locked=True))).scalars())
            for doc in docs:
                ACK.transition(doc, Ack.TIMEOUT, actor=uow.actor, events=uow.events,
                               reason=f"no 997 within {minutes} minutes (FR-OB-23)")
                order = (await s.execute(select(Order).join(DocumentLink, and_(
                    DocumentLink.entity_id == Order.id, DocumentLink.entity_type == "order",
                    DocumentLink.document_id == doc.id)).with_for_update())).scalar_one()
                if order.fulfillment_status == F.SENT_940:
                    FULFILLMENT.transition(order, F.TIMEOUT_940, actor=uow.actor, events=uow.events, reason="no 997 (FR-OB-23)")
                sent = doc.sent_at.astimezone(TORONTO).strftime("%Y-%m-%d %H:%M")
                await self._task(uow, EDI_940_TIMEOUT, order, f"No 997 for order {order.order_number}",
                                 f"No 997 arrived within {minutes} minutes for the 940 of order {order.order_number} "
                                 f"(partner {order.partner_code}, {order.source_order_number}), sent {sent} Toronto time "
                                 f"(control {doc.st02}). It is not resent automatically; check with Radiant.")
                report.timeouts += 1
            await uow.commit()
        return report

    async def check_after_940(self, uow, order: Order, new_lines: dict[str, dict[str, Any]],
                              new_ship_to: dict[str, Any] | None) -> bool:
        return await changed_after_940(uow, self.tasks, order, new_lines, new_ship_to)


async def latest_940(session, order_id: int, *, for_update: bool = False) -> Document | None:
    """The order's newest 940 that still counts (not cancelled or replaced)."""
    q = (select(Document).join(DocumentLink, and_(DocumentLink.document_id == Document.id,
                                                  DocumentLink.entity_type == "order", DocumentLink.entity_id == order_id))
         .where(Document.kind == "940_EDI", Document.direction == "OUT",
                Document.file_status.not_in((OutboundFile.CANCELLED.value, OutboundFile.REPLACED.value)))
         .order_by(Document.id.desc()).limit(1))
    if for_update:
        q = q.with_for_update(of=Document)
    return (await session.execute(q)).scalar_one_or_none()


# ── a source change after the 940 (FR-OB-16, AC-OB-18, AC-OB-70) ────────────
async def changed_after_940(uow, tasks, order: Order, new_lines: dict[str, dict[str, Any]],
                            new_ship_to: dict[str, Any] | None) -> bool:
    """Compare what the source now says with the values the 940 locked; a
    difference raises an Action Required task with both. The 940 is never
    resent by itself (FR-OB-16)."""
    doc = await latest_940(uow.session, order.id)
    if doc is None:
        return False
    locked = (doc.body_json or {}).get("locked") or {}
    before = locked.get("lines", {})
    changes = []
    for no in sorted(set(before) | set(new_lines), key=int):
        b, a = before.get(no), new_lines.get(no)
        if b != a:
            was = f"{b['qty_ea']} EA {b['item_code']}" if b else "—"
            now = f"{a['qty_ea']} EA {a['item_code']}" if a else "removed"
            changes.append(f"line {no}: 940 {was} → now {now}")
    old_st = locked.get("ship_to") or {}
    new_st = Address.of(new_ship_to).__dict__ if new_ship_to else {}
    diff_st = [k for k in ("name", "address1", "address2", "city", "province", "zip")
               if (old_st.get(k) or "") != (new_st.get(k) or "")]
    if diff_st:
        changes.append("ship-to " + ", ".join(f"{k}: {old_st.get(k) or '—'} → {new_st.get(k) or '—'}" for k in diff_st))
    if not changes:
        return False
    await tasks.raise_problem(uow.session, CHANGED_AFTER_940, order.id, problem(
        CHANGED_AFTER_940, f"Order changed after its 940: {order.order_number}",
        f"Order {order.order_number} ({order.partner_code} {order.source_order_number}) changed at the source after "
        f"its 940 (control {doc.st02}, {doc.file_status}). The 940 values stay as sent:\n" + "\n".join(changes)
        + "\nAgree any change with Radiant; a replacement 940 is sent only with Send 940 now → confirm replacement "
        "(FR-OB-03c).", urgent=True))
    return True
