"""The scheduled cycle (design §4.1 / §4.2, FR-OB-07, FR-OB-50).

P2 runs step 1 — fetch every active SFTP connection (intake) — and then the
outbox worker; the later steps join in their phases. Single flight, the stale
closure (AC-OB-46) and the run record are the platform's `ScheduledJob`; the
interval and pause are read from the settings on every tick. One connection's
failure never stops the others (AC-OB-47); a latched connection is skipped
quietly — it was reported when it latched (AC-OB-48).
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta

from sqlalchemy import select

from app.models.config import Connection
from app.platform.actors import SYSTEM_ACTOR
from app.platform.jobs import RunSummary, Schedule, ScheduledJob
from app.services import oplog
from app.services.intake import ConnectionSnapshot, Intake, PassReport
from app.services.settings import SCHEDULER_INTERVAL_MINUTES, SCHEDULER_PAUSED, SETTINGS

# pg advisory lock key of the cycle; unique across the shared database.
CYCLE_LOCK_KEY = 0x5A1E5001
STALE_AFTER = timedelta(minutes=30)


def settings_schedule(uow_factory):
    async def schedule() -> Schedule:
        async with uow_factory(SYSTEM_ACTOR) as uow:
            minutes = await SETTINGS.get(uow.session, SCHEDULER_INTERVAL_MINUTES)
            paused = await SETTINGS.get(uow.session, SCHEDULER_PAUSED)
        if minutes is None:  # a bad stored interval is safe-off: paused
            return Schedule(timedelta(minutes=5), paused=True)
        return Schedule(timedelta(minutes=minutes), paused=paused)
    return schedule


class SalesCycle(ScheduledJob):
    name = "sales_cycle"
    lock_key = CYCLE_LOCK_KEY

    def __init__(self, *, intake: Intake, outbox_worker, shopify_intake=None, edi_intake=None, outbound=None,
                 shadow=None, shipments=None, **kw) -> None:
        super().__init__(stale_after=STALE_AFTER, **kw)
        self.intake = intake
        self.shopify_intake = shopify_intake
        self.edi_intake = edi_intake
        self.outbound = outbound
        self.shadow = shadow
        self.shipments = shipments
        self.outbox_worker = outbox_worker

    async def run_once(self, run) -> RunSummary:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            active = [ConnectionSnapshot.of(c) for c in (await uow.session.execute(
                select(Connection).where(Connection.active.is_(True)).order_by(Connection.name)
            )).scalars()]
        conns = [c for c in active if c.kind == "SFTP"]
        stores = [c for c in active if c.kind == "SHOPIFY"]
        await oplog.write_alone(self.uow_factory, "INFO", "cycle", f"cycle started ({run.trigger}): "
                                f"{len(conns)} SFTP connection(s), {len(stores)} Shopify store(s)", run_id=run.id)
        per: dict[str, dict] = {}
        trouble = 0
        for c in conns:
            try:
                r = await self.intake.run_pass(c, run_id=run.id)
            except Exception as e:  # noqa: BLE001 — never expected; the pass catches its own failures
                await oplog.write_alone(self.uow_factory, "ERROR", "cycle", f"{c.name}: pass failed: {e}",
                                        run_id=run.id, connection_id=c.id)
                r = PassReport(status="list_failed")
            per[c.name] = r.as_dict()
            trouble += r.failed + (r.status == "list_failed")
        # Step 1 for Shopify, then step 2: the stored 850s become orders (§4.2).
        shopify: dict[str, dict] = {}
        for c in stores if self.shopify_intake else []:
            try:
                r = await self.shopify_intake.run_pass(c, run_id=run.id)
                shopify[c.name] = r.as_dict()
                trouble += r.failed + (r.status in ("fetch_failed", "not_advanced"))
            except Exception as e:  # noqa: BLE001 — never expected; the pass catches its own failures
                await oplog.write_alone(self.uow_factory, "ERROR", "cycle", f"{c.name}: Shopify pass failed: {e}",
                                        run_id=run.id, connection_id=c.id)
                trouble += 1
        edi = await self.edi_intake.run_pass(run_id=run.id) if self.edi_intake else {}
        trouble += edi.get("failed", 0)
        # P4 / P5: the orders that qualify get their 940 (queued in the outbox);
        # the 997s and 945s that arrived are applied (a 945 queues its Shopify
        # write-back); the outbox sends; late 940s time out — after the 945s,
        # so a 945 that arrived stops the clock.
        edi_out: dict = {}
        if self.outbound:
            queued = await self.outbound.queue_due(run_id=run.id)
            edi_out["queue"] = asdict(queued)
            trouble += queued.failed
            edi_out["997"] = asdict(await self.outbound.process_997s(run_id=run.id))
        if self.shipments:
            s945 = await self.shipments.process_945s(run_id=run.id)
            edi_out["945"] = asdict(s945)
            trouble += s945.failed
        outbox = asdict(await self.outbox_worker.run_once())
        trouble += outbox.get("failed", 0)
        if self.outbound:
            edi_out["timeouts"] = asdict(await self.outbound.timeouts(run_id=run.id))
        # The shadow run (§5.4): Zoho's reference files in, then the comparison.
        shadow: dict = {}
        if self.shadow:
            try:
                shadow = asdict(await self.shadow.run(run_id=run.id))
                trouble += shadow.get("not_imported", 0)
            except Exception as e:  # noqa: BLE001 — never expected; the comparison catches per reference
                await oplog.write_alone(self.uow_factory, "ERROR", "cycle", f"shadow comparison failed: {e}", run_id=run.id)
                trouble += 1
        counts = {"connections": per, "shopify": shopify, "edi_850": edi, "940": edi_out, "outbox": outbox,
                  "shadow": shadow}
        await oplog.write_alone(self.uow_factory, "INFO", "cycle", "cycle finished", run_id=run.id, context=counts)
        return RunSummary(counts=counts, partial=trouble > 0)
