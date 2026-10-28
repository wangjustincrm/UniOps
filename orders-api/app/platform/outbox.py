"""Outbox for external effects (design §13.7).

An SFTP put, a call to an external API or an external email is never made
inside the business transaction. It is written as an outbox row in that same
transaction and executed by `OutboxWorker` after commit, so a crash between
commit and the call loses nothing.

Each kind has a retry policy (product owner, 2026-10-27):

- `ONCE` — attempted exactly once; a failure marks the row FAILED and calls
  the kind's `on_failed` (mark the document Failed, raise a task). Only a
  person retries (`Outbox.requeue`). Used for file puts and API writes, where
  a timeout may hide a request that actually succeeded.
- `retry_while_not_accepted(n, backoff)` — retried only while the remote side
  has not accepted the message (`NotAccepted`). Once accepted, a later error is
  `AcceptedThenFailed`: the row is DONE and the error only logged — it is
  never resent.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError, NotFound

logger = logging.getLogger(__name__)

PENDING, DONE, FAILED = "PENDING", "DONE", "FAILED"


class NotAccepted(Exception):
    """The remote side did not take the message (connection failed, or it was
    refused before the transfer completed). Nothing was delivered."""


class AcceptedThenFailed(Exception):
    """The remote side accepted the message; something after that failed.
    The message was delivered and must not be sent again."""


class OutboxRecordMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=PENDING, index=True)
    # Attempts in the current round; a manual requeue starts a new round.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    retry_on: tuple[type[BaseException], ...] = ()
    backoff_seconds: tuple[int, ...] = (60,)

    def should_retry(self, exc: BaseException, attempts: int) -> bool:
        return attempts < self.max_attempts and isinstance(exc, self.retry_on)

    def delay(self, attempts: int) -> timedelta:
        i = min(max(attempts - 1, 0), len(self.backoff_seconds) - 1)
        return timedelta(seconds=self.backoff_seconds[i])


ONCE = RetryPolicy(max_attempts=1)


def retry_while_not_accepted(max_attempts: int, backoff_seconds: tuple[int, ...] = (60, 300, 900)) -> RetryPolicy:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    return RetryPolicy(max_attempts=max_attempts, retry_on=(NotAccepted,), backoff_seconds=backoff_seconds)


@dataclass(frozen=True)
class OutboxContext:
    row_id: int
    kind: str
    attempt: int
    manual: bool  # a person asked for this attempt; e.g. read remote state before writing


Effect = Callable[[dict[str, Any], OutboxContext], Awaitable[None]]
OnFailed = Callable[[Any, BaseException, Any], Awaitable[None]]  # (row, exc, uow)


@dataclass(frozen=True)
class OutboxKind:
    effect: Effect
    policy: RetryPolicy = ONCE
    on_failed: OnFailed | None = None


class Outbox:
    def __init__(self, model: type) -> None:
        self.model = model

    def add(self, session, kind: str, payload: dict[str, Any]) -> Any:
        """Queue an effect in the caller's transaction."""
        row = self.model(kind=kind, payload=payload, status=PENDING, attempts=0, manual=False)
        session.add(row)
        return row

    async def requeue(self, session, row_id: int) -> Any:
        """A person's retry of a FAILED row: one new round under its policy."""
        row = (await session.execute(
            select(self.model).where(self.model.id == row_id).with_for_update()
        )).scalar_one_or_none()
        if row is None:
            raise NotFound("outbox", row_id)
        if row.status != FAILED:
            raise BusinessError("outbox.not_failed", f"outbox row {row_id} is {row.status}", {"status": row.status})
        row.status = PENDING
        row.attempts = 0
        row.manual = True
        row.next_attempt_at = datetime.now(timezone.utc)
        row.finished_at = None
        return row


@dataclass
class OutboxSummary:
    done: int = 0
    retry_scheduled: int = 0
    failed: int = 0

    @property
    def processed(self) -> int:
        return self.done + self.retry_scheduled + self.failed


class OutboxWorker:
    """Executes due rows one at a time, each in its own unit of work with the
    row locked (`FOR UPDATE SKIP LOCKED`, so two workers never take the same
    row)."""

    def __init__(
        self,
        outbox: Outbox,
        kinds: Mapping[str, OutboxKind],
        uow_factory: Callable[..., Any],
        *,
        batch: int = 50,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.outbox = outbox
        self.kinds = kinds
        self.uow_factory = uow_factory
        self.batch = batch
        self.clock = clock

    async def run_once(self) -> OutboxSummary:
        summary = OutboxSummary()
        for _ in range(self.batch):
            outcome = await self._one()
            if outcome is None:
                break
            setattr(summary, outcome, getattr(summary, outcome) + 1)
        return summary

    async def _one(self) -> str | None:
        m = self.outbox.model
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            now = self.clock()
            row = (await uow.session.execute(
                select(m)
                .where(m.status == PENDING, m.next_attempt_at <= now)
                .order_by(m.next_attempt_at, m.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )).scalar_one_or_none()
            if row is None:
                return None
            outcome = await self._execute(uow, row, now)
            await uow.commit()
            return outcome

    async def _execute(self, uow, row, now: datetime) -> str:
        kind = self.kinds.get(row.kind)
        row.attempts += 1
        if kind is None:
            return await self._fail(uow, row, LookupError(f"no handler for outbox kind {row.kind!r}"), None, now)
        ctx = OutboxContext(row_id=row.id, kind=row.kind, attempt=row.attempts, manual=row.manual)
        try:
            await kind.effect(row.payload, ctx)
        except AcceptedThenFailed as e:
            # Delivered: never resend. The error is kept for the log only.
            logger.warning("outbox %s (%s) delivered, later step failed: %s", row.id, row.kind, e)
            row.last_error = f"after delivery: {type(e).__name__}: {e}"
            return self._done(row, now)
        except Exception as e:  # noqa: BLE001 — every failure is recorded on the row
            if kind.policy.should_retry(e, row.attempts):
                row.last_error = f"{type(e).__name__}: {e}"
                row.next_attempt_at = now + kind.policy.delay(row.attempts)
                logger.info("outbox %s (%s) attempt %s not accepted; retry at %s",
                            row.id, row.kind, row.attempts, row.next_attempt_at)
                return "retry_scheduled"
            return await self._fail(uow, row, e, kind, now)
        return self._done(row, now)

    @staticmethod
    def _done(row, now: datetime) -> str:
        row.status = DONE
        row.finished_at = now
        return "done"

    async def _fail(self, uow, row, exc: BaseException, kind: OutboxKind | None, now: datetime) -> str:
        row.status = FAILED
        row.finished_at = now
        row.last_error = f"{type(exc).__name__}: {exc}"
        logger.warning("outbox %s (%s) FAILED after %s attempt(s): %s", row.id, row.kind, row.attempts, exc)
        if kind is not None and kind.on_failed is not None:
            await kind.on_failed(row, exc, uow)
        return "failed"
