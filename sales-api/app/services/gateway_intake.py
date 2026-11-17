"""Authorize.Net pull (Finance, user 2026-11-17): one pass over one Authorize.Net connection.

Read-only. From the connection's cursor (the settlement time read through; NULL reads nothing —
a person sets where to start, as for Shopify) it asks for the settled batches in windows of at
most 31 days, oldest first:

1. Each batch list response is stored as a GATEWAY_JSON document (identical bytes once), and each
   batch upserted as a `sales_gateway_settlements` row (its per-card statistics added up).
2. A batch whose transactions were not read whole yet has its transaction list read, page by
   page (each page a document), and its transactions upserted — in one unit per batch, so a
   batch is either all there or read again next pass.
3. The cursor moves to the last batch read whole (or to the end of an empty window that is fully
   in the past), never backwards. A failure stops the pass there: the next one carries on.
   Each pass reads at most MAX_BATCHES batches, so a long history arrives over a few passes.
4. Matching (domain/gateway_match.py) links settled transactions to the Shopify payments they
   are. A transaction still unmatched after that has its details read (invoice number,
   description) in rounds of DETAILS_PER_PASS, matching again after each, until none is left or
   DETAILS_BUDGET_SECONDS is spent — a first history read drains in a pass or two.

A refused login, or the Transaction Details API being off, latches the connection like a refused
SFTP login: it is skipped until someone clears it on the Connections page.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select, update

from app.domain import gateway_match as gm
from app.domain.gateways import kind_of, settled_of
from app.domain.status import InboundFile
from app.integrations.authorize_net import MAX_DAYS, AuthorizeNetAuthFailed, readable
from app.models.config import Connection
from app.models.documents import Document
from app.models.gateways import GatewaySettlement, GatewayTransaction
from app.models.orders import Order
from app.models.payments import OrderPayment
from app.platform.actors import SYSTEM_ACTOR
from app.platform.pipeline import isolated
from app.services import oplog
from app.services.intake import ConnectionSnapshot
from app.services.settings import SETTINGS, SHADOW_MODE

STEP = "authorize_net"
GATEWAY = "AUTHORIZE_NET"
SHOPIFY_GATEWAYS = ("authorize_net", "authorize.net")  # the old gateway and the new app
CURRENCY = "CAD"           # the merchant account's currency (its batches carry none); said on the Connections form
OVERLAP = timedelta(days=1)  # each pass reads the last day again: a batch is never missed
MAX_BATCHES = 400          # per pass (~3–4 minutes; user 2026-11-17)
# The batch list answers nothing before Jan 1 of two years back (E00013, seen 2026-11-17: "First
# Settlement Date can not be older than the year of 2024"): a start before it is moved up to it.
HISTORY_YEARS = 2
DETAILS_PER_PASS = 200     # transaction details read per round, for the unmatched only
# A pass keeps reading rounds (matching after each) until none is left or this budget is spent, so
# a first history read (~4,000 details) drains in a pass or two, never by hand (review 2026-11-17).
DETAILS_BUDGET_SECONDS = 600


@dataclass
class GatewayReport:
    windows: int = 0
    batches: int = 0
    batches_read: int = 0      # transaction lists read whole this pass
    transactions: int = 0
    details_read: int = 0
    matched: int = 0
    failed: int = 0
    status: str = "ok"         # ok | read_off | cursor_not_set | latched | fetch_failed | more_to_read
    cursor_before: str | None = None
    cursor_after: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def text(self) -> str:
        return (f"{self.batches} batches, {self.transactions} transactions, {self.matched} matched, {self.failed} errors"
                + ("" if self.status == "ok" else f" ({self.status})"))


def _money(v: Any) -> Decimal:
    try:
        return Decimal(str(v if v not in (None, "") else 0)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        return Decimal("0.00")


def _when(v: Any) -> datetime | None:
    if not v:
        return None
    t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def batch_totals(b: dict[str, Any]) -> dict[str, Any]:
    """A settled batch's statistics (one row per card type), added up."""
    stats = b.get("statistics") or []
    total = lambda k: sum((_money(s.get(k)) for s in stats), Decimal("0.00"))  # noqa: E731
    count = lambda k: sum(int(s.get(k) or 0) for s in stats)  # noqa: E731
    sales, refunds = total("chargeAmount"), total("refundAmount")
    return {"sales_amount": sales, "sales_count": count("chargeCount"), "refund_amount": refunds,
            "refund_count": count("refundCount"), "net_amount": sales - refunds,
            "declined_count": count("declineCount"), "voided_count": count("voidCount"),
            # Not in Net: shown on their own so a difference with the bank has a visible reason.
            "chargeback_amount": total("chargebackAmount"), "chargeback_count": count("chargebackCount"),
            "returned_amount": total("returnedItemAmount"), "returned_count": count("returnedItemCount")}


# What is matched to Shopify: money that moved (settled sales and refunds), and voids (the sale they undid).
MATCHABLE = or_(GatewayTransaction.settled.is_(True) & GatewayTransaction.kind.in_(("sale", "refund")),
                GatewayTransaction.kind == "void")


def old_gateway_id(p: OrderPayment) -> str | None:
    """The processor's id Shopify's old Authorize.Net gateway kept ("120474651006#1000#purchase"). A
    "#void" refund is the void of its sale, not a refund: it takes no id (the sale has it)."""
    if (p.gateway or "").lower() != "authorize_net" or not p.authorization or p.authorization.endswith("#void"):
        return None
    return p.authorization.split("#")[0] or None


def _bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


class AuthorizeNetIntake:
    def __init__(self, *, uow_factory, gateway, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 timer: Callable[[], float] = time.monotonic, budget_seconds: float = DETAILS_BUDGET_SECONDS):
        self.uow_factory = uow_factory
        self.gateway = gateway
        self.clock = clock
        self.timer, self.budget_seconds = timer, budget_seconds

    async def _log(self, level: str, message: str, conn, run_id, **context) -> None:
        await oplog.write_alone(self.uow_factory, level, STEP, message, run_id=run_id, connection_id=conn.id,
                                context=context or None)

    async def run_pass(self, conn: ConnectionSnapshot, *, run_id: int | None = None) -> GatewayReport:
        report = GatewayReport(cursor_before=conn.cursor_updated_at.isoformat() if conn.cursor_updated_at else None)
        report.cursor_after = report.cursor_before
        if conn.auth_failed_at is not None:
            report.status = "latched"
            return report
        if not conn.can_read:
            report.status = "read_off"
            return report
        if conn.cursor_updated_at is None:
            report.status = "cursor_not_set"
            return report
        now = self.clock()
        cursor = conn.cursor_updated_at
        earliest = datetime(now.year - HISTORY_YEARS, 1, 1, tzinfo=timezone.utc)
        start = max(cursor - OVERLAP, earliest)
        if cursor - OVERLAP < earliest:
            await self._log("INFO", f"{conn.name}: Authorize.Net keeps batches from {earliest.date()}; reading from there",
                            conn, run_id)
        while start < now:
            end = min(start + timedelta(days=MAX_DAYS - 1), now)
            report.windows += 1
            try:
                batches = await self.gateway.settled_batches(conn, start, end)
            except AuthorizeNetAuthFailed as e:
                await self._latch(conn, str(e), run_id)
                report.status = "latched"
                break
            except Exception as e:  # noqa: BLE001 — one gateway never stops the cycle
                report.status, report.failed = "fetch_failed", report.failed + 1
                await self._log("ERROR", f"{conn.name}: reading the settled batches failed: {readable(e)}", conn, run_id,
                                error_type=type(e).__name__)
                break
            await self._store(conn, {"batchList": batches}, f"settled batches {start.date()}..{end.date()}")
            batches.sort(key=lambda b: b.get("settlementTimeUTC") or "")
            stopped = False
            for b in batches:
                if report.batches_read >= MAX_BATCHES:
                    report.status, stopped = "more_to_read", True
                    break
                report.batches += 1
                ok = await self._batch(conn, b, report, run_id)
                if ok is None:  # latched: what was read whole so far still counts
                    stopped = True
                    break
                if not ok:
                    report.failed += 1
                    report.status, stopped = "fetch_failed", True
                    break
                at = _when(b.get("settlementTimeUTC"))
                if at is not None and at > cursor:
                    cursor = at
            if stopped:
                break
            if end < now - OVERLAP and end > cursor:  # an empty stretch fully in the past is done
                cursor = end
            start = end
        if cursor > conn.cursor_updated_at:
            await self._advance(conn, cursor, run_id)
            report.cursor_after = cursor.isoformat()
        if report.status != "latched":
            report.matched += await self.match(conn.id)
            started = self.timer()
            while self.timer() - started < self.budget_seconds:
                n = await self._read_details(conn, run_id)
                report.details_read += n
                if n:
                    report.matched += await self.match(conn.id)
                if n < DETAILS_PER_PASS:  # none left (or a refusal stopped the round): the next pass carries on
                    break
        return report

    async def _batch(self, conn: ConnectionSnapshot, b: dict[str, Any], report: GatewayReport, run_id) -> bool | None:
        """Upsert one batch; read its transactions unless they were read whole before. False: failed;
        None: the login was refused (latched)."""
        batch_id = str(b.get("batchId") or "")
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            done = (await uow.session.execute(select(GatewaySettlement.transactions_read_at).where(
                GatewaySettlement.gateway == GATEWAY, GatewaySettlement.external_id == batch_id))).scalar_one_or_none()
        txs: list[dict[str, Any]] | None = None
        if done is None:
            txs, page = [], 1
            try:
                while True:
                    p = await self.gateway.batch_transactions(conn, batch_id, page)
                    await self._store(conn, {"batchId": batch_id, "page": page, "transactions": p.transactions},
                                      f"batch {batch_id} transactions page {page}")
                    txs.extend(p.transactions)
                    if not p.transactions or len(txs) >= p.total:
                        break
                    page += 1
            except AuthorizeNetAuthFailed as e:
                await self._latch(conn, str(e), run_id)
                report.status = "latched"
                return None
            except Exception as e:  # noqa: BLE001
                await self._log("ERROR", f"{conn.name}: reading batch {batch_id} failed: {readable(e)}", conn, run_id,
                                batch_id=batch_id, error_type=type(e).__name__)
                return False

        async def write(uow) -> int:
            s = uow.session
            uow.set_reason("Authorize.Net settled batch")
            row = (await s.execute(select(GatewaySettlement).where(
                GatewaySettlement.gateway == GATEWAY, GatewaySettlement.external_id == batch_id))).scalar_one_or_none()
            if row is None:
                row = GatewaySettlement(connection_id=conn.id, gateway=GATEWAY, external_id=batch_id, currency=CURRENCY,
                                        settled_at=_when(b.get("settlementTimeUTC")) or self.clock(), raw=b)
                s.add(row)
            row.settled_at = _when(b.get("settlementTimeUTC")) or row.settled_at
            row.state, row.payment_method, row.raw = b.get("settlementState"), b.get("paymentMethod"), b
            for k, v in batch_totals(b).items():
                setattr(row, k, v)
            await s.flush()
            if txs is None:
                return 0
            for t in txs:
                await self._upsert_tx(s, conn, row.id, t)
            row.transactions_read_at = self.clock()
            await s.flush()
            return len(txs)

        outcome = await isolated(f"{conn.name}:batch {batch_id}", write, uow_factory=self.uow_factory,
                                 log=self._failed(conn, f"storing batch {batch_id}", run_id))
        if not outcome.ok:
            return False
        report.transactions += outcome.result
        report.batches_read += int(txs is not None)
        return True

    async def _upsert_tx(self, s, conn: ConnectionSnapshot, settlement_id: int | None, t: dict[str, Any]) -> GatewayTransaction:
        ext = str(t.get("transId") or "")
        row = (await s.execute(select(GatewayTransaction).where(
            GatewayTransaction.gateway == GATEWAY, GatewayTransaction.external_id == ext))).scalar_one_or_none()
        status = t.get("transactionStatus")
        if row is None:
            row = GatewayTransaction(connection_id=conn.id, gateway=GATEWAY, external_id=ext, kind=kind_of(status),
                                     amount=_money(t.get("settleAmount")), currency=CURRENCY, raw=t)
            s.add(row)
        row.settlement_id = settlement_id if settlement_id is not None else row.settlement_id
        row.kind, row.status, row.settled = kind_of(status), status, settled_of(status)
        row.amount = _money(t.get("settleAmount"))
        row.submitted_at = _when(t.get("submitTimeUTC")) or row.submitted_at
        row.card_type = t.get("accountType") or row.card_type
        row.invoice_number = (t.get("invoiceNumber") or row.invoice_number or None)
        row.raw = {**(row.raw or {}), **t} if row.details_read_at else t
        return row

    async def _read_details(self, conn: ConnectionSnapshot, run_id, limit: int | None = None) -> int:
        """The details of settled (or voided) transactions still unmatched (their order fields), a few per pass."""
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            todo = list((await uow.session.execute(select(GatewayTransaction.id, GatewayTransaction.external_id).where(
                GatewayTransaction.connection_id == conn.id, MATCHABLE,
                GatewayTransaction.payment_row_id.is_(None), GatewayTransaction.details_read_at.is_(None))
                .order_by(GatewayTransaction.submitted_at.desc()).limit(limit or DETAILS_PER_PASS))).all())
        read = 0
        for row_id, ext in todo:
            try:
                d = await self.gateway.transaction_details(conn, ext)
            except AuthorizeNetAuthFailed as e:
                await self._latch(conn, str(e), run_id)
                break
            except Exception as e:  # noqa: BLE001 — the next pass tries again
                await self._log("WARNING", f"{conn.name}: details of transaction {ext} not read: {readable(e)}", conn, run_id)
                break

            async def write(uow, row_id=row_id, d=d) -> None:
                row = await uow.session.get(GatewayTransaction, row_id)
                uow.set_reason("Authorize.Net transaction details")
                order = d.get("order") or {}
                row.invoice_number = order.get("invoiceNumber") or row.invoice_number
                parts = [order.get("description"), order.get("purchaseOrderNumber")]
                row.description = " | ".join(str(x) for x in parts if x) or row.description
                row.ref_external_id = d.get("refTransId") or row.ref_external_id
                row.details_read_at = self.clock()
                row.raw = {**(row.raw or {}), "details": d}

            if (await isolated(f"{conn.name}:details {ext}", write, uow_factory=self.uow_factory,
                               log=self._failed(conn, f"storing details of {ext}", run_id))).ok:
                read += 1
        return read

    async def match(self, connection_id: int) -> int:
        """Link the connection's unmatched settled (or voided) transactions to the unmatched Shopify payments."""
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            s = uow.session
            txs = (await s.execute(select(GatewayTransaction).where(
                GatewayTransaction.connection_id == connection_id, MATCHABLE,
                GatewayTransaction.payment_row_id.is_(None)))).scalars().all()
            if not txs:
                return 0
            taken = select(GatewayTransaction.payment_row_id).where(GatewayTransaction.payment_row_id.is_not(None))
            rows = (await s.execute(select(OrderPayment, Order.source_order_number).join(Order, Order.id == OrderPayment.order_id).where(
                func.lower(OrderPayment.gateway).in_(SHOPIFY_GATEWAYS), OrderPayment.status == "success",
                OrderPayment.kind.in_(("sale", "capture", "refund")), OrderPayment.test.is_(False),
                OrderPayment.id.not_in(taken)))).all()
            payments = [gm.ShopifyPayment(
                id=p.id, kind="refund" if p.kind == "refund" else "sale", amount=p.amount, processed_at=p.processed_at,
                gateway_id=old_gateway_id(p), payment_id=p.payment_id, order_number=number) for p, number in rows]
            # A voided transaction is the Shopify sale it voided (kind "sale" for matching).
            found = gm.match([gm.GatewayTx(key=t.id, kind="sale" if t.kind == "void" else t.kind, amount=t.amount,
                                           submitted_at=t.submitted_at,
                                           texts=(t.invoice_number or "", t.description or ""), external_id=t.external_id,
                                           final=t.details_read_at is not None, void=t.kind == "void")
                              for t in txs], payments)
            if not found:
                return 0
            uow.set_reason("match gateway transactions to Shopify payments")
            by_id = {t.id: t for t in txs}
            now = self.clock()
            for m in found:
                t = by_id[m.tx]
                t.payment_row_id, t.match_method, t.matched_at = m.payment, m.method, now
            await s.flush()
            await uow.commit()
            return len(found)

    async def _store(self, conn: ConnectionSnapshot, value: Any, name: str) -> int | None:
        """A response as received (minus card and personal data): identical bytes kept once."""
        data = _bytes(value)
        sha = hashlib.sha256(data).hexdigest()
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            s = uow.session
            found = (await s.execute(select(Document.id).where(Document.sha256 == sha, Document.content.is_not(None))
                                     )).scalar_one_or_none()
            if found is not None:
                return found
            doc = Document(direction="IN", origin="PARTNER", system="AUTHORIZE_NET", connection_id=conn.id,
                           kind="GATEWAY_JSON", remote_name=name[:255], size=len(data), sha256=sha, content=data,
                           processing_status=InboundFile.PROCESSED.value, shadow=await SETTINGS.get(s, SHADOW_MODE))
            s.add(doc)
            await s.flush()
            await uow.commit()
            return doc.id

    async def _advance(self, conn: ConnectionSnapshot, cursor: datetime, run_id) -> None:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            await uow.session.execute(update(Connection).where(Connection.id == conn.id).values(cursor_updated_at=cursor))
            await oplog.write(uow.session, "INFO", STEP, f"{conn.name}: read through {cursor.isoformat()}",
                              run_id=run_id, connection_id=conn.id)
            await uow.commit()

    def _failed(self, conn, what: str, run_id):
        async def log(outcome) -> None:
            await self._log("ERROR", f"{conn.name}: {what} failed: {outcome.error_type}: {outcome.message}"[:1000], conn, run_id)
        return log

    async def _latch(self, conn, reason: str, run_id) -> None:
        async with self.uow_factory(SYSTEM_ACTOR, reason="authorize.net auth failed") as uow:
            c = await uow.session.get(Connection, conn.id, with_for_update=True)
            c.auth_failed_at, c.auth_failed_reason = self.clock(), reason[:2000]
            await oplog.write(uow.session, "ERROR", STEP, f"{conn.name}: login refused; connection latched: {reason}",
                              run_id=run_id, connection_id=conn.id)
            await uow.commit()
