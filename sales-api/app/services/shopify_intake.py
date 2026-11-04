"""Shopify pull (FR-OB-10, design §4.4): one pass over one store.

Pages from the inclusive `updated_at` cursor, oldest update first. Each page
is stored as a SHOPIFY_ORDER_JSON document (FR-OB-05) — identical bytes are
kept once — and each order is upserted in a unit of its own. The boundary
order returns on every fetch and is recognised as unchanged (AC-OB-10). The
cursor advances only when the whole batch is committed, to the batch maximum,
never backwards; any failure leaves it where it was. An empty cursor pulls
nothing (the user sets where to start). A refused login latches the
connection (FR-OB-50). Read-only: nothing is written to Shopify in P3.

The cursor is system-written: advanced with a plain UPDATE (no version bump
or audit row every five minutes) and logged; a person resets it through the
audited "reset cursor" action.

Finance (user 2026-11-03): with every order it imports, the pass reads the
order's transactions and stores its payments, refunds and summary
(services/shopify_finance.py); a failure there fails the order like any other
(the cursor stays). After the pages it fills the finance of up to
REFRESH_PER_PASS orders already here whose finance is missing or older than
the order (each GETs the order and its transactions). Reads only.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from app.domain.sources import SourceError, from_shopify
from app.domain.status import InboundFile
from app.integrations.shopify import ShopifyAuthFailed, page_bytes, readable
from app.models.config import Connection, Partner
from app.models.documents import Document
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.platform.pipeline import isolated
from app.services import oplog
from app.services.settings import SETTINGS, SHADOW_MODE
from app.services.intake import ConnectionSnapshot
from app.services.orders import BarcodeLookup, OrderSync
from app.services.problems import SHOPIFY_AUTH_FAILED, SHOPIFY_FETCH_FAILED, problem
from app.services.shopify_finance import REFRESH_PER_PASS, stale_finance, store_finance

STEP = "shopify"
DEFAULT_PAGE_SIZE = 50


@dataclass
class ShopifyReport:
    pages: int = 0
    orders: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    finance_refreshed: int = 0  # orders already here whose finance was filled
    finance_failed: int = 0
    status: str = "ok"  # ok | cursor_not_set | latched | fetch_failed | not_advanced
    cursor_before: str | None = None
    cursor_after: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ShopifyIntake:
    def __init__(self, *, uow_factory, shopify, tasks, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.uow_factory = uow_factory
        self.shopify = shopify
        self.tasks = tasks
        self.clock = clock

    async def _log(self, level: str, message: str, conn, run_id, **context) -> None:
        await oplog.write_alone(self.uow_factory, level, STEP, message, run_id=run_id, connection_id=conn.id,
                                context=context or None)

    async def run_pass(self, conn: ConnectionSnapshot, *, run_id: int | None = None) -> ShopifyReport:
        report = ShopifyReport(cursor_before=conn.cursor_updated_at.isoformat() if conn.cursor_updated_at else None)
        if conn.auth_failed_at is not None:
            report.status = "latched"
            return report
        if conn.cursor_updated_at is None:
            report.status = "cursor_not_set"
            return report
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            partner = (await uow.session.execute(select(Partner).where(Partner.code == conn.partner_code))).scalar_one_or_none()
        abbrev = partner.abbrev if partner else None
        barcodes = BarcodeLookup(self.shopify, conn)  # one read per variant per run
        batch_max: datetime | None = None
        page_info: str | None = None
        while True:
            try:
                page = await self.shopify.orders_page(
                    conn, updated_at_min=None if page_info else conn.cursor_updated_at.isoformat(),
                    page_info=page_info, limit=conn.page_size or DEFAULT_PAGE_SIZE)
            except ShopifyAuthFailed as e:
                await self._latch(conn, str(e), run_id)
                report.status = "latched"
                return report
            except Exception as e:  # noqa: BLE001 — one store never stops the cycle
                why = readable(e)
                report.status = "fetch_failed"
                await self._log("ERROR", f"{conn.name}: fetching orders failed: {why}", conn, run_id,
                                error_type=type(e).__name__)
                await self._raise(SHOPIFY_FETCH_FAILED, conn.id, f"Shopify fetch failed: {conn.name}", why)
                return report
            report.pages += 1
            doc_id = await self._store_page(conn, page.orders, report.pages)
            for o in page.orders:
                report.orders += 1
                outcome = await isolated(f"{conn.name}:order {o.get('id')}",
                                         lambda uow, o=o: self._one(uow, conn, o, abbrev, doc_id, barcodes),
                                         uow_factory=self.uow_factory, log=self._order_failed(conn, o, run_id))
                if not outcome.ok:
                    report.failed += 1
                    continue
                setattr(report, outcome.result, getattr(report, outcome.result) + 1)
                ts = o.get("updated_at")
                if ts:
                    t = datetime.fromisoformat(ts)
                    batch_max = t if batch_max is None or t > batch_max else batch_max
            page_info = page.next_page_info
            if not page_info:
                break
        await self._refresh_finance(conn, report, run_id)
        if report.failed:
            report.status = "not_advanced"  # AC-OB-10: a failure leaves the cursor where it was
            return report
        if batch_max is not None and batch_max > conn.cursor_updated_at:  # never backwards, inclusive
            async with self.uow_factory(SYSTEM_ACTOR) as uow:
                await uow.session.execute(update(Connection).where(Connection.id == conn.id)
                                          .values(cursor_updated_at=batch_max))
                await oplog.write(uow.session, "INFO", STEP, f"{conn.name}: cursor advanced to {batch_max.isoformat()}",
                                  run_id=run_id, connection_id=conn.id)
                await uow.commit()
            report.cursor_after = batch_max.isoformat()
        else:
            report.cursor_after = report.cursor_before
        return report

    async def _one(self, uow, conn: ConnectionSnapshot, o: dict, abbrev, doc_id, barcodes: BarcodeLookup) -> str:
        src = from_shopify(o, partner_code=conn.partner_code, partner_abbrev=abbrev, cutoff=conn.order_number_cutoff)
        key = (Order.partner_code == src.partner_code, Order.source_order_id == src.source_order_id)
        existing = (await uow.session.execute(select(Order.source_updated_at, Order.finance_updated_at).where(*key))).one_or_none()
        if existing is not None and existing[0] is not None and src.updated_at == existing[0]:
            if existing[1] != existing[0]:  # here before its finance was read
                await self._finance(uow, conn, o, key)
            return "unchanged"  # the boundary order, seen again (AC-OB-10)
        await OrderSync(uow, self.tasks).upsert(src, connection_id=conn.id, document_id=doc_id, received_at=self.clock(),
                                                barcodes=barcodes)
        await self._finance(uow, conn, o, key)
        return "updated" if existing is not None else "created"

    async def _finance(self, uow, conn, o: dict, key) -> None:
        order = (await uow.session.execute(select(Order).where(*key))).scalar_one()
        await store_finance(uow, order, o, await self.shopify.transactions(conn, str(o["id"])))

    async def _refresh_finance(self, conn: ConnectionSnapshot, report: ShopifyReport, run_id) -> None:
        """Up to REFRESH_PER_PASS orders already here whose finance is missing or stale."""
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            todo = (await uow.session.execute(stale_finance(conn.id).limit(REFRESH_PER_PASS))).all()

        async def one(uow, order_id: int, source_id: str) -> bool:
            o = await self.shopify.order(conn, source_id)
            order = await uow.session.get(Order, order_id)
            changed = datetime.fromisoformat(o["updated_at"]) if o.get("updated_at") else None
            if changed is not None and order.source_updated_at is not None and changed != order.source_updated_at:
                return False  # changed since it was imported: the next pass's pages bring it whole
            await store_finance(uow, order, o, await self.shopify.transactions(conn, source_id))
            return True

        for order_id, source_id in todo:
            outcome = await isolated(f"{conn.name}:finance {source_id}", lambda uow, i=order_id, sid=source_id: one(uow, i, sid),
                                     uow_factory=self.uow_factory, log=self._finance_failed(conn, source_id, run_id))
            if outcome.ok:
                report.finance_refreshed += int(bool(outcome.result))
            else:
                report.finance_failed += 1
                if isinstance(outcome.error, ShopifyAuthFailed):
                    break  # the pages already latched or will; no point asking again

    async def _store_page(self, conn, orders: list[dict], n: int) -> int | None:
        """The page as received (FR-OB-05). Identical bytes are kept once: a page
        that is only the unchanged boundary order is not stored again."""
        data = page_bytes({"orders": orders})
        sha = hashlib.sha256(data).hexdigest()
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            s = uow.session
            found = (await s.execute(select(Document.id).where(Document.sha256 == sha, Document.content.is_not(None))
                                     )).scalar_one_or_none()
            if found is not None:
                return found
            doc = Document(direction="IN", origin="PARTNER", system="SHOPIFY", connection_id=conn.id,
                           kind="SHOPIFY_ORDER_JSON", remote_name=f"orders page {n} from {conn.cursor_updated_at.isoformat()}",
                           size=len(data), sha256=sha, content=data, processing_status=InboundFile.PROCESSED.value,
                           shadow=await SETTINGS.get(s, SHADOW_MODE))
            s.add(doc)
            await s.flush()
            await uow.commit()
            return doc.id

    def _order_failed(self, conn, o: dict, run_id):
        async def log(outcome) -> None:
            why = outcome.message if isinstance(outcome.error, SourceError) else f"{outcome.error_type}: {outcome.message}"
            await self._log("ERROR", f"{conn.name}: Shopify order {o.get('name') or o.get('id')} failed: {why}",
                            conn, run_id, order_id=o.get("id"))
            await self._raise(SHOPIFY_FETCH_FAILED, conn.id, f"Shopify order failed: {conn.name}",
                              f"Order {o.get('name') or o.get('id')}: {why}. The cursor stays until it imports.")
        return log

    def _finance_failed(self, conn, source_id: str, run_id):
        async def log(outcome) -> None:
            await self._log("ERROR", f"{conn.name}: payments and refunds of Shopify order {source_id} not read: "
                            f"{outcome.error_type}: {outcome.message}", conn, run_id, order_id=source_id)
        return log

    async def _raise(self, event, entity, title, description) -> None:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            await self.tasks.raise_problem(uow.session, event, entity, problem(event, title, description))
            await uow.commit()

    async def _latch(self, conn, reason: str, run_id) -> None:
        async with self.uow_factory(SYSTEM_ACTOR, reason="shopify auth failed") as uow:
            await uow.session.execute(update(Connection).where(Connection.id == conn.id).values(
                auth_failed_at=self.clock(), auth_failed_reason=reason[:2000]))
            await oplog.write(uow.session, "ERROR", STEP, f"{conn.name}: login refused; connection latched: {reason}",
                              run_id=run_id, connection_id=conn.id)
            await self.tasks.raise_problem(uow.session, SHOPIFY_AUTH_FAILED, conn.id, problem(
                SHOPIFY_AUTH_FAILED, f"Shopify login refused: {conn.name}",
                f"{reason}. The store is skipped until someone clears it on the Connections page.", urgent=True))
            await uow.commit()
