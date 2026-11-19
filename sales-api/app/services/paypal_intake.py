"""PayPal pull (Finance, user 2026-11-19): one pass over one PayPal connection, read-only.

From the connection's cursor (NULL reads nothing — a person sets where to start) it asks Transaction
Search for the account's transactions in windows of at most 31 days, oldest first, every page of each:

1. Each page is stored as a GATEWAY_JSON document (identical bytes once), already reduced to what is kept
   (integrations/paypal.py: no payer). A window's transactions are upserted in one unit.
2. Each transaction becomes a `sales_gateway_transactions` row: a payment received is a sale, its refund a
   refund (domain/gateways.paypal_kind); a fee, a currency conversion, a transfer to the bank, a hold or a
   chargeback is "other" — shown, never in the sales or refunds, never matched. PayPal's fee is kept per
   transaction (`fee_amount`: what PayPal took, so a refund that gave the fee back is negative).
3. The cursor moves to the end of the last window read whole, never past the time PayPal says it has
   everything up to (`last_refreshed_datetime`: it publishes a transaction some hours late), and never
   backwards; each pass reads the last day again (OVERLAP).
4. A window with more transactions than PayPal returns at once (RESULTSET_TOO_LARGE) is read in halves.
5. Matching: Shopify keeps PayPal's own transaction id (`authorization`) on every PayPal payment, so
   TRANSACTION_ID finds them; the base's weaker rules are there for the rest.

A refused Client ID / Secret, or Transaction Search not enabled for the REST app, latches the connection.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import aliased

from app.domain.gateways import paypal_kind, paypal_settled
from app.integrations.paypal import MAX_DAYS, PayPalAuthFailed, PayPalRequestRefused, readable
from app.models.gateways import GatewayTransaction
from app.models.payments import OrderPayment
from app.platform.actors import SYSTEM_ACTOR
from app.platform.pipeline import isolated
from app.services.gateway_base import GatewayIntake, GatewayReport, money, when
from app.services.intake import ConnectionSnapshot

STEP = "paypal"
GATEWAY = "PAYPAL"
MAX_WINDOWS = 40           # per pass (~3 years at 31 days): a first history read arrives in a pass or two
MIN_WINDOW = timedelta(hours=1)  # a window is halved down to this when PayPal says it has too many results
HISTORY_DAYS = 3 * 365 - 7  # PayPal answers for about three years


def external_id(transaction_id: str, event_code: str, kind: str) -> str:
    """PayPal lists several events under one transaction id (a hold T1110 and its release T1111, seen on dev2
    2026-11-19): a sale or refund keeps PayPal's id (what Shopify has, what matching looks for); any other event
    is "<id>:<event code>", so each is its own row."""
    return transaction_id if kind in ("sale", "refund") else f"{transaction_id}:{event_code}"[:64]


def _value(m: Any) -> str | None:
    return m.get("value") if isinstance(m, dict) else None


class PayPalIntake(GatewayIntake):
    GATEWAY, SYSTEM, STEP, LABEL = GATEWAY, "PAYPAL", STEP, "PayPal"
    SHOPIFY_GATEWAYS = ("paypal",)
    AUTH_FAILED = PayPalAuthFailed
    MAX_DAYS = MAX_DAYS
    HISTORY = staticmethod(lambda now: (now - timedelta(days=HISTORY_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0))
    readable = staticmethod(readable)

    async def after_match(self, conn: ConnectionSnapshot, report: GatewayReport, run_id) -> None:
        """Option A (user 2026-11-19): each transfer to the bank a settlement, the rest in the PayPal balance."""
        from app.services import paypal_settlements
        report.batches = await paypal_settlements.rebuild(self.uow_factory, conn.id, self.clock())

    def shopify_gateway_id(self, p: OrderPayment) -> str | None:
        """Shopify keeps PayPal's own transaction id (17 letters and digits) as the payment's authorization."""
        a = (p.authorization or "").strip()
        return a or None

    async def read(self, conn: ConnectionSnapshot, report: GatewayReport, run_id) -> datetime:
        now = self.clock()
        cursor = conn.cursor_updated_at
        start = await self.start(conn, run_id)
        span = timedelta(days=MAX_DAYS - 1)
        while start < now and report.windows < MAX_WINDOWS:
            end = min(start + span, now)
            read = await self._window(conn, start, end, report, run_id)
            if read is None:  # latched or failed: what was read whole so far still counts
                break
            if read == "too_large":
                if end - start <= MIN_WINDOW:
                    report.status, report.failed = "fetch_failed", report.failed + 1
                    await self._log("ERROR", f"{conn.name}: PayPal has too many transactions even for "
                                    f"{start.isoformat()}..{end.isoformat()}", conn, run_id)
                    break
                span = (end - start) / 2
                continue
            # Done up to here — but never past what PayPal has published yet.
            through = min(end, read) if isinstance(read, datetime) else end
            if through > cursor:
                cursor = through
            start, span = end, timedelta(days=MAX_DAYS - 1)
        else:
            if start < now:
                report.status = "more_to_read"
        await self._suffix_bare_others(conn.id)
        await self._funded_refunds(conn.id)
        return cursor

    async def _suffix_bare_others(self, connection_id: int) -> None:
        """An "other" event the first version stored under PayPal's bare id (dev2: 5, read before 6a722b76, never read
        again) takes its "<id>:<event code>" too (user 2026-11-19), unless that row already exists: one shape for all."""
        T = GatewayTransaction
        async with self.uow_factory(SYSTEM_ACTOR, reason="PayPal: one id shape for other events") as uow:
            s = uow.session
            bare = (await s.execute(select(T).where(T.connection_id == connection_id, T.gateway == GATEWAY, T.kind == "other",
                                                    ~T.external_id.contains(":")))).scalars().all()
            for t in bare:
                code = str((t.raw or {}).get("transaction_event_code") or "")
                new = external_id(t.external_id, code, "other")
                if code and not (await s.execute(select(T.id).where(T.gateway == GATEWAY, T.external_id == new))).first():
                    t.external_id = new
            await s.flush()
            await uow.commit()

    async def _funded_refunds(self, connection_id: int) -> None:
        """A refund PayPal still calls pending (P) whose money came from the bank — a successful deposit (T03xx) that
        names the refund as its reference, same time, same amount: the balance was short — is done (user 2026-11-19:
        three on dev2, 90.77). Run after every read, since a re-read sets the refund's own status again."""
        T, D = GatewayTransaction, aliased(GatewayTransaction)
        funded = select(D.id).where(D.connection_id == connection_id, D.gateway == GATEWAY, D.kind == "other",
                                    D.raw["transaction_event_code"].astext.like("T03%"), D.status == "S",
                                    D.raw["paypal_reference_id"].astext == T.external_id, D.amount == T.amount).exists()
        async with self.uow_factory(SYSTEM_ACTOR, reason="PayPal refund funded from the bank") as uow:
            await uow.session.execute(update(T).where(
                T.connection_id == connection_id, T.gateway == GATEWAY, T.kind == "refund", T.settled.is_(False),
                T.status == "P", funded).values(settled=True))
            await uow.commit()

    async def _window(self, conn: ConnectionSnapshot, start: datetime, end: datetime, report: GatewayReport, run_id):
        """All pages of one window, upserted in one unit. Returns the time PayPal has everything up to (or the
        window's end if it does not say), "too_large", or None when the pass must stop."""
        report.windows += 1
        txs: list[dict[str, Any]] = []
        refreshed: datetime | None = None
        page = 1
        try:
            while True:
                p = await self.gateway.transactions(conn, start, end, page)
                await self._store(conn, {"start": start.isoformat(), "end": end.isoformat(), "page": page,
                                         "transactions": p.transactions}, f"transactions {start.date()}..{end.date()} page {page}")
                txs.extend(p.transactions)
                refreshed = when(p.refreshed_at) or refreshed
                if page >= p.total_pages or not p.transactions:
                    break
                page += 1
        except PayPalAuthFailed as e:
            await self._latch(conn, str(e), run_id)
            report.status = "latched"
            return None
        except PayPalRequestRefused as e:
            if "RESULTSET_TOO_LARGE" in str(e):
                return "too_large"
            report.status, report.failed = "fetch_failed", report.failed + 1
            await self._log("ERROR", f"{conn.name}: reading {start.date()}..{end.date()} failed: {readable(e)}", conn, run_id)
            return None
        except Exception as e:  # noqa: BLE001 — one gateway never stops the cycle
            report.status, report.failed = "fetch_failed", report.failed + 1
            await self._log("ERROR", f"{conn.name}: reading {start.date()}..{end.date()} failed: {readable(e)}", conn, run_id,
                            error_type=type(e).__name__)
            return None

        # One row per event: a repeat in the window (a page read twice, an event updated) is its latest.
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for t in txs:
            key = (str(t.get("transaction_id") or ""), str(t.get("transaction_event_code") or ""))
            if key not in latest or str(t.get("transaction_updated_date") or "") >= str(latest[key].get("transaction_updated_date") or ""):
                latest[key] = t

        async def write(uow) -> int:
            s = uow.session
            uow.set_reason("PayPal transactions")
            for t in latest.values():
                await self._upsert_tx(s, conn, t)
                await s.flush()  # the next event of the same id sees this one
            return len(latest)

        outcome = await isolated(f"{conn.name}:paypal {start.date()}", write, uow_factory=self.uow_factory,
                                 log=self._failed(conn, f"storing {start.date()}..{end.date()}", run_id))
        if not outcome.ok:
            report.status, report.failed = "fetch_failed", report.failed + 1
            return None
        report.transactions += outcome.result
        return refreshed or end

    async def _upsert_tx(self, s, conn: ConnectionSnapshot, t: dict[str, Any]) -> GatewayTransaction | None:
        tid = str(t.get("transaction_id") or "")
        if not tid:
            return None
        code = str(t.get("transaction_event_code") or "")
        value = _value(t.get("transaction_amount"))
        kind = paypal_kind(code, value)
        currency = ((t.get("transaction_amount") or {}).get("currency_code") or "CAD")[:3]
        amount = abs(money(value))
        ext = external_id(tid, code, kind)
        if ext != tid:  # stored under the bare id by an earlier version: renamed, never a second row
            old = (await s.execute(select(GatewayTransaction).where(
                GatewayTransaction.gateway == GATEWAY, GatewayTransaction.external_id == tid,
                GatewayTransaction.kind == "other"))).scalar_one_or_none()
            if old is not None and (old.raw or {}).get("transaction_event_code") == code:
                old.external_id = ext
                await s.flush()
        row = await self._transaction(s, conn, ext, kind=kind, amount=amount, currency=currency, raw=t)
        status = t.get("transaction_status")
        row.kind, row.status, row.settled = kind, status, paypal_settled(status, kind)
        row.amount, row.currency = amount, currency
        fee = _value(t.get("fee_amount"))
        row.fee_amount = -money(fee) if fee not in (None, "") else None  # what PayPal took (a fee is sent negative)
        row.submitted_at = when(t.get("transaction_initiation_date")) or row.submitted_at
        row.card_type = t.get("instrument_sub_type") or t.get("instrument_type") or row.card_type
        row.invoice_number = t.get("invoice_id") or row.invoice_number
        row.description = t.get("custom_field") or row.description
        row.ref_external_id = t.get("paypal_reference_id") or row.ref_external_id
        row.details_read_at = row.details_read_at or self.clock()  # everything PayPal has is in the search
        row.raw = t
        return row
