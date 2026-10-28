import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.platform.actors import SYSTEM_ACTOR
from app.platform.jobs import FAILED, PARTIAL, RUNNING, SUCCEEDED, Background, RunSummary, Schedule, ScheduledJob
from tests.platform.conftest import RunRow


class Job(ScheduledJob):
    name = "pt_job"
    lock_key = 990_001

    def __init__(self, *a, gate: asyncio.Event | None = None, outcome="ok", **kw):
        super().__init__(*a, **kw)
        self.ran = 0
        self.gate = gate
        self.outcome = outcome

    async def run_once(self, run):
        self.ran += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.outcome == "boom":
            raise RuntimeError("boom")
        return RunSummary(counts={"units": 3}, partial=self.outcome == "partial")


def _job(uow_factory, *, interval=timedelta(minutes=5), paused=False, **kw):
    async def schedule():
        return Schedule(interval=interval, paused=paused)
    return Job(run_model=RunRow, uow_factory=uow_factory, schedule=schedule, **kw)


async def _runs(uow_factory):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        return (await uow.session.execute(select(RunRow).order_by(RunRow.id))).scalars().all()


async def test_tick_runs_and_closes_with_summary(uow_factory):
    job = _job(uow_factory)
    run = await job.tick()
    assert run.status == SUCCEEDED and run.summary == {"units": 3} and job.ran == 1


async def test_not_due_until_interval_elapsed_and_paused_skips(uow_factory):
    job = _job(uow_factory)
    await job.tick()
    assert await job.tick() is None and job.ran == 1
    assert (await job.tick(trigger="MANUAL", force=True)).status == SUCCEEDED  # run now
    paused = _job(uow_factory, interval=timedelta(0), paused=True)
    assert await paused.tick() is None and paused.ran == 0


async def test_single_flight_while_a_run_is_running(uow_factory):
    gate = asyncio.Event()
    first = _job(uow_factory, interval=timedelta(0), gate=gate)
    second = _job(uow_factory, interval=timedelta(0))
    t = asyncio.ensure_future(first.tick())
    for _ in range(50):
        if first.ran:
            break
        await asyncio.sleep(0.02)
    assert await second.tick() is None and second.ran == 0
    gate.set()
    assert (await t).status == SUCCEEDED


async def test_stale_running_run_is_closed_as_failed(uow_factory, clean):
    job = _job(uow_factory, interval=timedelta(0), stale_after=timedelta(minutes=10))
    async with uow_factory(SYSTEM_ACTOR) as uow:
        now = job.clock()
        uow.session.add(RunRow(job="pt_job", trigger="SCHEDULE", status=RUNNING,
                               started_at=now - timedelta(hours=1), heartbeat_at=now - timedelta(hours=1)))
        await uow.commit()
    run = await job.tick()
    runs = await _runs(uow_factory)
    assert runs[0].status == FAILED and "stale" in runs[0].error
    assert run.status == SUCCEEDED


async def test_fresh_running_run_blocks_a_new_one(uow_factory):
    job = _job(uow_factory, interval=timedelta(0))
    async with uow_factory(SYSTEM_ACTOR) as uow:
        now = job.clock()
        uow.session.add(RunRow(job="pt_job", trigger="SCHEDULE", status=RUNNING, started_at=now, heartbeat_at=now))
        await uow.commit()
    assert await job.tick() is None and job.ran == 0


async def test_failed_and_partial_runs(uow_factory):
    assert (await _job(uow_factory, interval=timedelta(0), outcome="boom").tick()).status == FAILED
    assert (await _job(uow_factory, interval=timedelta(0), outcome="partial").tick()).status == PARTIAL


async def test_advisory_lock_held_elsewhere_means_no_run(uow_factory, db_engine):
    job = _job(uow_factory, interval=timedelta(0))
    async with db_engine.connect() as conn:
        await conn.execute(select(1))
        from sqlalchemy import text
        await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": Job.lock_key})
        try:
            assert await job.tick() is None
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": Job.lock_key})


async def test_heartbeat_moves_while_running(uow_factory):
    gate = asyncio.Event()
    job = _job(uow_factory, interval=timedelta(0), gate=gate, heartbeat_every=0.05)
    t = asyncio.ensure_future(job.tick())
    await asyncio.sleep(0.4)
    gate.set()
    run = await t
    assert run.heartbeat_at > run.started_at


async def test_background_drain_stops_loops():
    bg = Background()
    ticks = []

    async def loop():
        while not bg.stop.is_set():
            ticks.append(1)
            await asyncio.sleep(0.01)

    bg.spawn(loop(), name="l")
    await asyncio.sleep(0.05)
    await bg.drain(timeout=1)
    n = len(ticks)
    await asyncio.sleep(0.05)
    assert n > 0 and len(ticks) == n
