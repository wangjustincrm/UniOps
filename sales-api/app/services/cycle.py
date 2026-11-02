"""The scheduler (design §4.1 / §4.2, FR-OB-07, FR-OB-50), per connection
(user decision 2026-11-02).

Two kinds of job, each a platform `ScheduledJob` with its own single flight
and run records (`sales_runs.scope`):

- a **connection job** per active connection ("connection:<id>"): due when
  its next run time is reached, it is not paused and the time is inside its
  window (app/domain/schedule.py). It does that connection's step — the SFTP
  pass, or the Shopify pull for a store — then takes the documents it just
  stored all the way through: 850 → orders, 997 / 945 / 944 applied (the
  shadow rules hold in each step). Its last run and next run time are written
  on the connection.
- the **system job** ("system"), every `scheduler.system_minutes`: the
  orders that qualify get their 940 and Ready transfers their 943 (queued),
  shipped orders move toward invoicing, the outbox sends, late 940 / 943s time
  out, and Zoho's reference files are compared during the shadow run.

`scheduler.paused` is the master switch for every job; the environment's
SALES_SCHEDULER_ENABLED decides whether the loop runs at all. One job's
failure never stops the others (AC-OB-47); a latched connection is skipped
quietly — it was reported when it latched (AC-OB-48). Single flight per job and
scope (AC-OB-46); "Run now" ignores the schedule, never the single flight.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.domain.schedule import Window, is_due, next_run, parse_days
from app.models.config import Connection
from app.platform.actors import SYSTEM_ACTOR
from app.platform.jobs import RunSummary, Schedule, ScheduledJob
from app.services import oplog
from app.services.intake import ConnectionSnapshot, Intake, PassReport
from app.services.settings import SCHEDULER_PAUSED, SETTINGS, SYSTEM_MINUTES

logger = logging.getLogger(__name__)

# pg advisory lock keys (with the scope's hash for a connection); unique across the shared database.
CONNECTION_LOCK_KEY = 0x5A1E5001
SYSTEM_LOCK_KEY = 0x5A1E5002
STALE_AFTER = timedelta(minutes=30)
SYSTEM = "system"


def scope_of(conn_id: int) -> str:
    return f"connection:{conn_id}"


def window_of(c: Connection) -> Window:
    try:
        days = parse_days(c.active_days)
    except ValueError:
        days = ()
    return Window(c.active_from, c.active_to, days)


def system_schedule(uow_factory):
    async def schedule() -> Schedule:
        async with uow_factory(SYSTEM_ACTOR) as uow:
            minutes = await SETTINGS.get(uow.session, SYSTEM_MINUTES)
            paused = await SETTINGS.get(uow.session, SCHEDULER_PAUSED)
        return Schedule(timedelta(minutes=minutes), paused=paused)
    return schedule


class ConnectionJob(ScheduledJob):
    name = "sales_connection"
    lock_key = CONNECTION_LOCK_KEY

    def __init__(self, conn_id: int, *, intake: Intake, shopify_intake=None, edi_intake=None, outbound=None,
                 shipments=None, transfers=None, **kw) -> None:
        super().__init__(stale_after=STALE_AFTER, **kw)
        self.conn_id = conn_id
        self.scope = scope_of(conn_id)
        self.intake, self.shopify_intake, self.edi_intake = intake, shopify_intake, edi_intake
        self.outbound, self.shipments, self.transfers = outbound, shipments, transfers

    async def due(self, session, now: datetime) -> bool:
        c = await session.get(Connection, self.conn_id)
        if c is None or not c.active or await SETTINGS.get(session, SCHEDULER_PAUSED):
            return False
        return is_due(now, next_run_at=c.next_run_at, paused=c.schedule_paused, window=window_of(c))

    async def run_once(self, run) -> RunSummary:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            c = await uow.session.get(Connection, self.conn_id)
            snap = ConnectionSnapshot.of(c)
        counts: dict = {"connection": snap.name, "kind": snap.kind}
        await oplog.write_alone(self.uow_factory, "INFO", "cycle", f"run started ({run.trigger}): {snap.name}",
                                run_id=run.id, connection_id=snap.id)
        try:
            summary = await self._steps(run, snap, counts)
        finally:
            await oplog.write_alone(self.uow_factory, "INFO", "cycle", f"run finished: {snap.name}", run_id=run.id,
                                    connection_id=snap.id, context=counts)
        return summary

    async def _steps(self, run, snap: ConnectionSnapshot, counts: dict) -> RunSummary:
        if snap.kind == "SHOPIFY":
            if self.shopify_intake is None:
                return RunSummary(counts=counts)
            r = await self.shopify_intake.run_pass(snap, run_id=run.id)
            counts["shopify"] = r.as_dict()
            trouble = r.failed + (r.status in ("fetch_failed", "not_advanced"))
            counts["text"] = f"{r.orders} orders, {r.failed} errors" + ("" if r.status == "ok" else f" ({r.status})")
            return RunSummary(counts=counts, partial=trouble > 0)
        p: PassReport = await self.intake.run_pass(snap, run_id=run.id)
        counts["pass"] = p.as_dict()
        trouble = p.failed + (p.status == "list_failed")
        # The documents this pass stored, all the way through (only this connection's).
        steps = (("850", self.edi_intake and self.edi_intake.run_pass),
                 ("997", self.outbound and self.outbound.process_997s),
                 ("945", self.shipments and self.shipments.process_945s),
                 ("944", self.transfers and self.transfers.process_944s))
        failed = 0
        for key, step in steps:
            if step is None:
                continue
            out = await step(run_id=run.id, connection_id=self.conn_id)
            out = out if isinstance(out, dict) else asdict(out)
            counts[key] = out
            failed += out.get("failed", 0)
        trouble += failed
        counts["text"] = f"{p.stored} files, {p.failed + failed} errors" + ("" if p.status == "ok" else f" ({p.status})")
        return RunSummary(counts=counts, partial=trouble > 0)

    async def on_closed(self, run) -> None:
        """The last run and the next run time, on the connection (also after a failure)."""
        # A Core UPDATE: the scheduler's bookkeeping is not a configuration
        # change — no audit row, and no new version (an open edit form stays valid).
        t = Connection.__table__
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            minutes = (await uow.session.execute(select(t.c.schedule_minutes).where(t.c.id == self.conn_id))).scalar_one_or_none()
            if minutes is None:
                return
            await uow.session.execute(update(t).where(t.c.id == self.conn_id).values(
                last_run_at=run.finished_at, last_run_status=run.status,
                last_run_summary=((run.summary or {}).get("text") or run.error or "")[:255] or None,
                last_error=run.error, next_run_at=next_run(run.started_at, minutes)))
            await uow.commit()


class SystemJob(ScheduledJob):
    name = "sales_system"
    lock_key = SYSTEM_LOCK_KEY
    scope = SYSTEM

    def __init__(self, *, outbox_worker, outbound=None, shadow=None, invoicing=None, transfers=None, **kw) -> None:
        super().__init__(stale_after=STALE_AFTER, **kw)
        self.outbox_worker, self.outbound, self.shadow = outbox_worker, outbound, shadow
        self.invoicing, self.transfers = invoicing, transfers

    async def run_once(self, run) -> RunSummary:
        await oplog.write_alone(self.uow_factory, "INFO", "cycle", f"run started ({run.trigger}): system job", run_id=run.id)
        out: dict = {}
        trouble = 0
        if self.outbound:  # the orders that qualify get their 940 (queued)
            q = await self.outbound.queue_due(run_id=run.id)
            out["940"] = asdict(q)
            trouble += q.failed
        if self.transfers:  # Ready To Send transfers get their 943
            t = await self.transfers.queue_ready(run_id=run.id)
            out["943"] = asdict(t)
            trouble += t.failed
        if self.invoicing:  # shipped orders move toward the delivery-confirmation worklist
            out["invoices"] = asdict(await self.invoicing.advance(run_id=run.id))
        ob = asdict(await self.outbox_worker.run_once())
        out["outbox"] = ob
        trouble += ob.get("failed", 0)
        if self.outbound:  # after the 945s, so a 945 that arrived stopped the clock
            out["940_timeouts"] = asdict(await self.outbound.timeouts(run_id=run.id))
        if self.transfers:
            out["943_timeouts"] = asdict(await self.transfers.timeouts(run_id=run.id))
        if self.shadow:  # the shadow run (§5.4): Zoho's reference files in, then the comparison
            try:
                sh = asdict(await self.shadow.run(run_id=run.id))
                out["shadow"] = sh
                trouble += sh.get("not_imported", 0)
            except Exception as e:  # noqa: BLE001 — never expected; the comparison catches per reference
                await oplog.write_alone(self.uow_factory, "ERROR", "cycle", f"shadow comparison failed: {e}", run_id=run.id)
                trouble += 1
        out["text"] = (f"{out.get('940', {}).get('queued', 0)} 940s queued, {ob.get('done', 0)} sent, "
                       f"{trouble} problems")
        await oplog.write_alone(self.uow_factory, "INFO", "cycle", "run finished: system job", run_id=run.id, context=out)
        return RunSummary(counts=out, partial=trouble > 0)


class SalesScheduler:
    """Ticks every job: each active connection's (when due), then the system
    job. A "Run now" of everything runs each active connection, then the system
    job — each through its own single flight."""

    def __init__(self, *, uow_factory, run_model, intake: Intake, outbox_worker, shopify_intake=None, edi_intake=None,
                 outbound=None, shadow=None, shipments=None, invoicing=None, transfers=None) -> None:
        self.uow_factory, self.run_model = uow_factory, run_model
        self.intake, self.shopify_intake, self.edi_intake = intake, shopify_intake, edi_intake
        self.outbound, self.shadow, self.shipments = outbound, shadow, shipments
        self.invoicing, self.transfers, self.outbox_worker = invoicing, transfers, outbox_worker
        self.system = SystemJob(outbox_worker=outbox_worker, outbound=outbound, shadow=shadow, invoicing=invoicing,
                                transfers=transfers, run_model=run_model, uow_factory=uow_factory,
                                schedule=system_schedule(uow_factory))

    def connection(self, conn_id: int) -> ConnectionJob:
        async def unused() -> Schedule:  # a connection's job is due by its own fields (ConnectionJob.due)
            return Schedule(timedelta(minutes=5))
        return ConnectionJob(conn_id, intake=self.intake, shopify_intake=self.shopify_intake, edi_intake=self.edi_intake,
                             outbound=self.outbound, shipments=self.shipments, transfers=self.transfers,
                             run_model=self.run_model, uow_factory=self.uow_factory, schedule=unused)

    async def active_connections(self) -> list[int]:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            return list((await uow.session.execute(select(Connection.id).where(Connection.active.is_(True))
                                                   .order_by(Connection.name))).scalars())

    async def tick(self) -> list:
        """One pass over every job; those not due (or running) are skipped."""
        runs = []
        for cid in await self.active_connections():
            runs.append(await self.connection(cid).tick())
        runs.append(await self.system.tick())
        return [r for r in runs if r is not None]

    async def run_everything(self, system_run_id: int) -> list:
        """The global "Run now": each active connection (one that is running is
        skipped), then the claimed system run."""
        runs = []
        for cid in await self.active_connections():
            r = await self.connection(cid).tick(trigger="MANUAL", force=True)
            if r is not None:
                runs.append(r)
        runs.append(await self.system.execute(system_run_id))
        return runs

    async def loop(self, stop: asyncio.Event, tick_seconds: float = 30.0) -> None:
        """Ticks until `stop` is set. One failing tick never ends the loop."""
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 — a job never raises; belt and braces
                logger.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=tick_seconds)
            except TimeoutError:
                pass
