"""Scheduled jobs (design §13.12) and the background-task holder.

A `ScheduledJob` ticks on a fixed short loop; each tick re-reads its interval
(and pause flag) from the settings, so a change takes effect without restart.
Single flight: a transaction-scoped advisory lock around claiming the run,
plus a RUNNING run row with a heartbeat. A RUNNING row whose heartbeat is
older than `stale_after` is closed as FAILED by the next claim.

Long-running loops are started with `Background.spawn` in the app lifespan and
stopped with `Background.drain` before the engine is disposed.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.actors import SYSTEM_ACTOR

logger = logging.getLogger(__name__)

RUNNING, SUCCEEDED, PARTIAL, FAILED = "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"


class RunRecordMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(10), nullable=False)  # SCHEDULE | MANUAL
    status: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


@dataclass
class RunSummary:
    counts: dict[str, int] = field(default_factory=dict)
    partial: bool = False  # some units failed; the run itself completed


@dataclass(frozen=True)
class Schedule:
    interval: timedelta
    paused: bool = False


Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ScheduledJob(ABC):
    name: str
    lock_key: int  # unique per job across the shared database

    def __init__(
        self,
        *,
        run_model: type,
        uow_factory: Callable[..., Any],
        schedule: Callable[[], Awaitable[Schedule]],
        stale_after: timedelta = timedelta(minutes=30),
        heartbeat_every: float = 30.0,
        clock: Clock = utcnow,
    ) -> None:
        self.run_model = run_model
        self.uow_factory = uow_factory
        self.schedule = schedule
        self.stale_after = stale_after
        self.heartbeat_every = heartbeat_every
        self.clock = clock

    @abstractmethod
    async def run_once(self, run: Any) -> RunSummary: ...

    # ── single flight ────────────────────────────────────────────────────────
    async def _claim(self, trigger: str, force: bool) -> Any | None:
        m = self.run_model
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            s = uow.session
            got = (await s.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": self.lock_key})).scalar_one()
            if not got:
                return None
            now = self.clock()
            await s.execute(
                update(m)
                .where(m.job == self.name, m.status == RUNNING, m.heartbeat_at < now - self.stale_after)
                .values(status=FAILED, finished_at=now, error="stale: heartbeat stopped")
            )
            running = (await s.execute(
                select(m.id).where(m.job == self.name, m.status == RUNNING).limit(1)
            )).scalar_one_or_none()
            if running is not None:
                await uow.commit()  # keep the stale closure
                return None
            if not force:
                sched = await self.schedule()
                last = (await s.execute(
                    select(m.started_at).where(m.job == self.name, m.trigger == "SCHEDULE")
                    .order_by(m.started_at.desc()).limit(1)
                )).scalar_one_or_none()
                if sched.paused or (last is not None and now - last < sched.interval):
                    await uow.commit()
                    return None
            run = m(job=self.name, trigger=trigger, status=RUNNING, started_at=now, heartbeat_at=now)
            s.add(run)
            await s.flush()
            run_id = run.id
            await uow.commit()
        return run_id

    async def _beat(self, run_id: int) -> None:
        """Moves the heartbeat while the run is RUNNING. A failed beat is
        logged and retried on the next one; the beat stops by itself once the
        run is no longer RUNNING (closed, or closed as stale elsewhere)."""
        m = self.run_model
        while True:
            await asyncio.sleep(self.heartbeat_every)
            try:
                async with self.uow_factory(SYSTEM_ACTOR) as uow:
                    res = await uow.session.execute(
                        update(m).where(m.id == run_id, m.status == RUNNING).values(heartbeat_at=self.clock())
                    )
                    await uow.commit()
                if res.rowcount == 0:
                    logger.warning("%s: run %s is no longer RUNNING; heartbeat stops", self.name, run_id)
                    return
            except Exception:  # noqa: BLE001
                logger.exception("%s: heartbeat for run %s failed", self.name, run_id)

    async def _close(self, run_id: int, status: str, summary: RunSummary | None, error: str | None) -> Any:
        """Closes the run; never raises (a run left RUNNING is closed as stale
        by a later claim)."""
        try:
            async with self.uow_factory(SYSTEM_ACTOR) as uow:
                run = await uow.session.get(self.run_model, run_id)
                run.status = status
                run.finished_at = self.clock()
                run.summary = dict(summary.counts) if summary else None
                run.error = error
                await uow.commit()
                return run
        except Exception:  # noqa: BLE001
            logger.exception("%s: could not close run %s as %s", self.name, run_id, status)
            return None

    async def tick(self, *, trigger: str = "SCHEDULE", force: bool = False) -> Any | None:
        """One claim-and-run. Returns the closed run, or None when not due,
        paused, or another run holds the job. Never raises an Exception; on
        cancellation (shutdown) the run is closed as FAILED "interrupted" so a
        restart is not blocked until the run goes stale."""
        run_id = await self.claim(trigger=trigger, force=force)
        if run_id is None:
            return None
        return await self.execute(run_id)

    async def claim(self, *, trigger: str = "MANUAL", force: bool = True) -> int | None:
        """The single-flight claim alone (a "Run now" claims in the request and
        runs `execute` in the background): the new RUNNING run's id, or None
        when another run holds the job (or, unforced, when not due)."""
        try:
            return await self._claim(trigger, force)
        except Exception:  # noqa: BLE001
            logger.exception("%s: could not claim a run", self.name)
            return None

    async def execute(self, run_id: int) -> Any | None:
        """Runs a claimed run to its end and closes it (see `tick`)."""
        beat = asyncio.ensure_future(self._beat(run_id))
        summary, status, error = None, FAILED, None
        try:
            async with self.uow_factory(SYSTEM_ACTOR) as uow:
                run = await uow.session.get(self.run_model, run_id)
            summary = await self.run_once(run)
            status = PARTIAL if summary.partial else SUCCEEDED
        except asyncio.CancelledError:
            error = "interrupted: the service stopped during the run"
            await self._stop_beat(beat)
            await self._close(run_id, FAILED, None, error)
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("%s: run %s failed", self.name, run_id)
            error = f"{type(e).__name__}: {e}"
        await self._stop_beat(beat)
        return await self._close(run_id, status, summary, error)

    @staticmethod
    async def _stop_beat(beat: asyncio.Future) -> None:
        beat.cancel()
        # The beat's own failure (if it had one) must not escape the tick.
        await asyncio.gather(beat, return_exceptions=True)

    async def loop(self, stop: asyncio.Event, tick_seconds: float = 30.0) -> None:
        """Ticks until `stop` is set. One failing tick never ends the loop."""
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 — tick should not raise; belt and braces
                logger.exception("%s: tick failed", self.name)
            try:
                await asyncio.wait_for(stop.wait(), timeout=tick_seconds)
            except TimeoutError:
                pass


class Background:
    """Holds the app's long-running tasks: started with `spawn` in the
    lifespan, stopped with `drain` before `engine.dispose()`. Nothing is
    fire-and-forget: every task is kept and awaited on shutdown."""

    def __init__(self) -> None:
        self.stop = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self, timeout: float = 10.0) -> None:
        self.stop.set()
        if not self._tasks:
            return
        done, pending = await asyncio.wait(set(self._tasks), timeout=timeout)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            if not t.cancelled() and t.exception() is not None:
                logger.error("background task %s ended with %r", t.get_name(), t.exception())
