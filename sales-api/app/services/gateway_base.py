"""What every payment gateway's pull shares (Finance; Authorize.Net 2026-11-17, PayPal 2026-11-19).

A gateway's intake (`GatewayIntake` subclass) reads one connection, read-only, into the shared tables
(`sales_gateway_settlements`, `sales_gateway_transactions`), and the same rules apply to every gateway:

- A pass reads nothing for a latched connection, one whose read is off, or one with no cursor (a
  person sets where to start, as for Shopify). It reads from the cursor less `OVERLAP`, never before
  the gateway's history, in windows of at most `MAX_DAYS`; the cursor only moves forward.
- Every response is stored as a GATEWAY_JSON document (identical bytes once), already reduced to what
  is kept (no card, no payer).
- A refused login latches the connection until someone clears it on the Connections page.
- Matching (domain/gateway_match.py) links the money that moved to the Shopify payments it is; what
  Shopify calls the gateway, and where Shopify keeps the gateway's own id, are the gateway's.

A subclass says who it is (`GATEWAY`, `SYSTEM`, `STEP`, `LABEL`, `SHOPIFY_GATEWAYS`, `AUTH_FAILED`,
`readable`) and how it reads (`read`); it may add work after matching (`after_match`).
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import func, or_, select, update

from app.domain import gateway_match as gm
from app.domain.status import InboundFile
from app.models.config import Connection
from app.models.documents import Document
from app.models.gateways import GatewayTransaction
from app.models.orders import Order
from app.models.payments import OrderPayment
from app.platform.actors import SYSTEM_ACTOR
from app.services import oplog
from app.services.intake import ConnectionSnapshot
from app.services.settings import SETTINGS, SHADOW_MODE

OVERLAP = timedelta(days=1)  # each pass reads the last day again: nothing is missed


@dataclass
class GatewayReport:
    windows: int = 0
    batches: int = 0           # settlements seen (Authorize.Net batches, PayPal transfers to the bank)
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
        return (f"{self.batches} settlements, {self.transactions} transactions, {self.matched} matched, {self.failed} errors"
                + ("" if self.status == "ok" else f" ({self.status})"))


def money(v: Any) -> Decimal:
    try:
        return Decimal(str(v if v not in (None, "") else 0)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        return Decimal("0.00")


def when(v: Any) -> datetime | None:
    """An ISO time as a gateway writes it ("…Z", "…+00:00" or PayPal's "…+0000"); naive is UTC."""
    if not v:
        return None
    s = str(v).replace("Z", "+00:00")
    if len(s) >= 5 and s[-5] in "+-" and s[-4:].isdigit():  # +0000 → +00:00
        s = f"{s[:-2]}:{s[-2:]}"
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


# What is matched to Shopify: money that moved (settled sales and refunds), and voids (the sale they undid).
MATCHABLE = or_(GatewayTransaction.settled.is_(True) & GatewayTransaction.kind.in_(("sale", "refund")),
                GatewayTransaction.kind == "void")


class GatewayIntake:
    GATEWAY: ClassVar[str]                    # sales_gateway_*.gateway
    SYSTEM: ClassVar[str]                     # the documents' system
    STEP: ClassVar[str]                       # the run log's step
    LABEL: ClassVar[str]                      # in messages: "Authorize.Net", "PayPal"
    SHOPIFY_GATEWAYS: ClassVar[tuple[str, ...]]  # what Shopify calls it (lower case)
    AUTH_FAILED: ClassVar[type[BaseException]]
    MAX_DAYS: ClassVar[int] = 31
    HISTORY: ClassVar[Callable[[datetime], datetime]]  # the earliest time the gateway answers for

    def __init__(self, *, uow_factory, gateway, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 timer: Callable[[], float] = time.monotonic, **_: Any):
        self.uow_factory = uow_factory
        self.gateway = gateway
        self.clock = clock
        self.timer = timer

    @staticmethod
    def readable(e: BaseException) -> str:
        raise NotImplementedError

    # ── the pass ───────────────────────────────────────────────────────────

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
        cursor = await self.read(conn, report, run_id)
        if cursor > conn.cursor_updated_at:
            await self._advance(conn, cursor, run_id)
            report.cursor_after = cursor.isoformat()
        if report.status != "latched":
            report.matched += await self.match(conn.id)
            await self.after_match(conn, report, run_id)
        return report

    async def read(self, conn: ConnectionSnapshot, report: GatewayReport, run_id) -> datetime:
        """Read from the cursor; return the time read through (the cursor if nothing new)."""
        raise NotImplementedError

    async def after_match(self, conn: ConnectionSnapshot, report: GatewayReport, run_id) -> None:
        """More to read for what is still unmatched (Authorize.Net: the transactions' details)."""

    async def start(self, conn: ConnectionSnapshot, run_id) -> datetime:
        """Where this pass reads from: the cursor less OVERLAP, not before the gateway's history."""
        earliest = type(self).HISTORY(self.clock())
        start = max(conn.cursor_updated_at - OVERLAP, earliest)
        if conn.cursor_updated_at - OVERLAP < earliest:
            await self._log("INFO", f"{conn.name}: {self.LABEL} keeps its history from {earliest.date()}; reading from there",
                            conn, run_id)
        return start

    # ── matching ───────────────────────────────────────────────────────────

    def shopify_gateway_id(self, p: OrderPayment) -> str | None:
        """The gateway's own id of a Shopify payment, where Shopify keeps it."""
        return None

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
                func.lower(OrderPayment.gateway).in_(self.SHOPIFY_GATEWAYS), OrderPayment.status == "success",
                OrderPayment.kind.in_(("sale", "capture", "refund")), OrderPayment.test.is_(False),
                OrderPayment.id.not_in(taken)))).all()
            payments = [gm.ShopifyPayment(
                id=p.id, kind="refund" if p.kind == "refund" else "sale", amount=p.amount, processed_at=p.processed_at,
                gateway_id=self.shopify_gateway_id(p), payment_id=p.payment_id, order_number=number) for p, number in rows]
            # A voided transaction is the Shopify sale it voided (kind "sale" for matching).
            found = gm.match([gm.GatewayTx(key=t.id, kind="sale" if t.kind == "void" else t.kind, amount=t.amount,
                                           submitted_at=t.submitted_at,
                                           texts=(t.invoice_number or "", t.description or ""), external_id=t.external_id,
                                           final=self.final(t), void=t.kind == "void")
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

    def final(self, t: GatewayTransaction) -> bool:
        """Nothing more will be read about it: amount and time may be tried."""
        return True

    # ── shared bookkeeping ─────────────────────────────────────────────────

    async def _log(self, level: str, message: str, conn, run_id, **context) -> None:
        await oplog.write_alone(self.uow_factory, level, self.STEP, message, run_id=run_id, connection_id=conn.id,
                                context=context or None)

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
            doc = Document(direction="IN", origin="PARTNER", system=self.SYSTEM, connection_id=conn.id,
                           kind="GATEWAY_JSON", remote_name=name[:255], size=len(data), sha256=sha, content=data,
                           processing_status=InboundFile.PROCESSED.value, shadow=await SETTINGS.get(s, SHADOW_MODE))
            s.add(doc)
            await s.flush()
            await uow.commit()
            return doc.id

    async def _advance(self, conn: ConnectionSnapshot, cursor: datetime, run_id) -> None:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            await uow.session.execute(update(Connection).where(Connection.id == conn.id).values(cursor_updated_at=cursor))
            await oplog.write(uow.session, "INFO", self.STEP, f"{conn.name}: read through {cursor.isoformat()}",
                              run_id=run_id, connection_id=conn.id)
            await uow.commit()

    def _failed(self, conn, what: str, run_id):
        async def log(outcome) -> None:
            await self._log("ERROR", f"{conn.name}: {what} failed: {outcome.error_type}: {outcome.message}", conn, run_id)
        return log

    async def _latch(self, conn, reason: str, run_id) -> None:
        async with self.uow_factory(SYSTEM_ACTOR, reason=f"{self.LABEL.lower()} auth failed") as uow:
            c = await uow.session.get(Connection, conn.id, with_for_update=True)
            c.auth_failed_at, c.auth_failed_reason = self.clock(), reason
            await oplog.write(uow.session, "ERROR", self.STEP, f"{conn.name}: login refused; connection latched: {reason}",
                              run_id=run_id, connection_id=conn.id)
            await uow.commit()

    async def _transaction(self, s, conn: ConnectionSnapshot, ext: str, **new: Any) -> GatewayTransaction:
        """The gateway's transaction row by its id, added (with `new`) if it is not there yet."""
        row = (await s.execute(select(GatewayTransaction).where(
            GatewayTransaction.gateway == self.GATEWAY, GatewayTransaction.external_id == ext))).scalar_one_or_none()
        if row is None:
            row = GatewayTransaction(connection_id=conn.id, gateway=self.GATEWAY, external_id=ext, **new)
            s.add(row)
        return row
