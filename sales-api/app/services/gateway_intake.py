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

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select, update

from app.domain.gateways import kind_of, settled_of
from app.integrations.authorize_net import MAX_DAYS, AuthorizeNetAuthFailed, AuthorizeNetError, readable
from app.models.gateways import GatewaySettlement, GatewayTransaction, details_attempts, details_failed_at
from app.models.payments import OrderPayment
from app.platform.actors import SYSTEM_ACTOR
from app.platform.pipeline import isolated
from app.services import oplog
from app.services.gateway_base import apply_changes, MATCHABLE, OVERLAP, GatewayIntake, GatewayReport, money as _money, when as _when
from app.services.intake import ConnectionSnapshot

__all__ = ["AuthorizeNetIntake", "GatewayReport", "batch_totals", "old_gateway_id"]

STEP = "authorize_net"
GATEWAY = "AUTHORIZE_NET"
SHOPIFY_GATEWAYS = ("authorize_net", "authorize.net")  # the old gateway and the new app
CURRENCY = "CAD"           # the merchant account's currency (its batches carry none); said on the Connections form
MAX_BATCHES = 400          # per pass (~3–4 minutes; user 2026-11-17)
# The batch list answers nothing before Jan 1 of two years back (E00013, seen 2026-11-17: "First
# Settlement Date can not be older than the year of 2024"): a start before it is moved up to it.
HISTORY_YEARS = 2
DETAILS_PER_PASS = 200     # transaction details read per round, for the unmatched only
# A pass keeps reading rounds (matching after each) until none is left or this budget is spent, so
# a first history read (~4,000 details) drains in a pass or two, never by hand (review 2026-11-17).
DETAILS_BUDGET_SECONDS = 600
# One transaction whose details Authorize.Net refuses is marked and skipped (review 2026-11-17): tried
# again after DETAILS_RETRY, at most DETAILS_ATTEMPTS times; then "Details not available".
DETAILS_ATTEMPTS = 3
DETAILS_RETRY = timedelta(hours=24)


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


def _about_this_one(e: BaseException) -> bool:
    """Authorize.Net answered and refused this request with its own code (not transport, not login)."""
    from app.integrations.authorize_net import AuthorizeNetRequestRefused
    return isinstance(e, AuthorizeNetRequestRefused)


def old_gateway_id(p: OrderPayment) -> str | None:
    """The processor's id Shopify's old Authorize.Net gateway kept ("120474651006#1000#purchase"). A
    "#void" refund is the void of its sale, not a refund: it takes no id (the sale has it)."""
    if (p.gateway or "").lower() != "authorize_net" or not p.authorization or p.authorization.endswith("#void"):
        return None
    return p.authorization.split("#")[0] or None


class AuthorizeNetIntake(GatewayIntake):
    GATEWAY, SYSTEM, STEP, LABEL = GATEWAY, "AUTHORIZE_NET", STEP, "Authorize.Net"
    SHOPIFY_GATEWAYS = SHOPIFY_GATEWAYS
    AUTH_FAILED = AuthorizeNetAuthFailed
    MAX_DAYS = MAX_DAYS
    HISTORY = staticmethod(lambda now: datetime(now.year - HISTORY_YEARS, 1, 1, tzinfo=timezone.utc))
    readable = staticmethod(readable)

    def __init__(self, *, uow_factory, gateway, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 timer: Callable[[], float] | None = None, budget_seconds: float = DETAILS_BUDGET_SECONDS):
        super().__init__(uow_factory=uow_factory, gateway=gateway, clock=clock, **({"timer": timer} if timer else {}))
        self.budget_seconds = budget_seconds

    def shopify_gateway_id(self, p: OrderPayment) -> str | None:
        return old_gateway_id(p)

    def final(self, t: GatewayTransaction) -> bool:
        return t.details_read_at is not None  # its details were read: nothing stronger can come

    async def read(self, conn: ConnectionSnapshot, report: GatewayReport, run_id) -> datetime:
        """The settled batches from the cursor, oldest first, each with its transactions."""
        now = self.clock()
        cursor = conn.cursor_updated_at
        start = await self.start(conn, run_id)
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
        return cursor

    async def after_match(self, conn: ConnectionSnapshot, report: GatewayReport, run_id) -> None:
        """The details of what is still unmatched, round after round, matching after each, within the budget."""
        started = self.timer()
        while self.timer() - started < self.budget_seconds:
            read, tried, stopped = await self._read_details(conn, run_id)
            report.details_read += read
            if read:
                report.matched += await self.match(conn.id)
            if stopped or tried < DETAILS_PER_PASS:  # none left, or Authorize.Net is unreachable: next pass
                break

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
            # Only what differs (gateway_base.apply_changes): a batch read again unchanged is no write.
            apply_changes(row, {"settled_at": _when(b.get("settlementTimeUTC")) or row.settled_at, "state": b.get("settlementState"),
                                "payment_method": b.get("paymentMethod"), "raw": b, **batch_totals(b)})
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
        status = t.get("transactionStatus")
        row = await self._transaction(s, conn, ext, kind=kind_of(status), amount=_money(t.get("settleAmount")),
                                      currency=CURRENCY, raw=t)
        row.settlement_id = settlement_id if settlement_id is not None else row.settlement_id
        row.kind, row.status, row.settled = kind_of(status), status, settled_of(status)
        row.amount = _money(t.get("settleAmount"))
        row.submitted_at = _when(t.get("submitTimeUTC")) or row.submitted_at
        row.card_type = t.get("accountType") or row.card_type
        row.invoice_number = (t.get("invoiceNumber") or row.invoice_number or None)
        keep = {k: row.raw[k] for k in ("details", "details_failed") if isinstance(row.raw, dict) and k in row.raw}
        row.raw = {**t, **keep}
        return row

    async def _read_details(self, conn: ConnectionSnapshot, run_id, limit: int | None = None) -> tuple[int, int, bool]:
        """The details of settled (or voided) transactions still unmatched (their order fields), one round.
        Returns (read, tried, stopped): stopped when Authorize.Net was unreachable or refused the login.
        A refusal for one transaction is recorded on it and the round goes on."""
        G = GatewayTransaction
        now = self.clock()
        due = or_(G.raw["details_failed"].is_(None) | (func.jsonb_typeof(G.raw["details_failed"]) != "object"),
                  (details_failed_at() < now - DETAILS_RETRY) & (details_attempts() < DETAILS_ATTEMPTS))
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            todo = list((await uow.session.execute(select(GatewayTransaction.id, GatewayTransaction.external_id).where(
                GatewayTransaction.connection_id == conn.id, MATCHABLE, due,
                GatewayTransaction.payment_row_id.is_(None), GatewayTransaction.details_read_at.is_(None))
                .order_by(GatewayTransaction.submitted_at.desc()).limit(limit or DETAILS_PER_PASS))).all())
        read = tried = 0
        for row_id, ext in todo:
            tried += 1
            try:
                d = await self.gateway.transaction_details(conn, ext)
            except AuthorizeNetAuthFailed as e:
                await self._latch(conn, str(e), run_id)
                return read, tried, True
            except AuthorizeNetError as e:
                if not _about_this_one(e):  # unreachable, 5xx, not JSON: the round stops, the next pass carries on
                    await self._log("WARNING", f"{conn.name}: details of transaction {ext} not read: {readable(e)}", conn, run_id)
                    return read, tried, True
                await self._details_failed(conn, row_id, ext, readable(e), run_id)
                continue
            except Exception as e:  # noqa: BLE001 — a shape this client cannot read: about this transaction only
                await self._details_failed(conn, row_id, ext, f"unexpected response shape ({type(e).__name__})", run_id)
                continue

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
            else:
                await self._details_failed(conn, row_id, ext, "its details could not be stored", run_id)
        return read, tried, False

    async def _details_failed(self, conn, row_id: int, ext: str, why: str, run_id) -> None:
        """Recorded on the transaction's raw.details_failed (Core UPDATE: bookkeeping, not an audited change);
        skipped until retried."""
        G = GatewayTransaction.__table__
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            raw = (await uow.session.execute(select(G.c.raw).where(G.c.id == row_id))).scalar_one()
            prev = raw.get("details_failed") if isinstance(raw.get("details_failed"), dict) else {}
            failed = {"at": self.clock().isoformat(), "attempts": int(prev.get("attempts") or 0) + 1, "error": why}
            await uow.session.execute(update(G).where(G.c.id == row_id).values(raw={**raw, "details_failed": failed}))
            await oplog.write(uow.session, "WARNING", STEP, f"{conn.name}: details of transaction {ext} refused: {why}",
                              run_id=run_id, connection_id=conn.id)
            await uow.commit()
