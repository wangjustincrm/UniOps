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

At most once. Before the effect runs, the row is committed as IN_FLIGHT with
its attempt counted; the outcome is written in a second transaction. If that
second transaction never happens (crash, lost connection, failed commit), the
row stays IN_FLIGHT and, once its lease has passed, is closed as FAILED with
"outcome unknown" and `on_failed` — never run again on its own. This holds for
every kind: an email whose answer was lost may have been accepted.

Suppressed (product owner, 2026-10-28). An effect refused by the outbound
guard because its switch is off (`Suppressed`) ends SUPPRESSED, not DONE, and
calls the kind's `on_suppressed`. Turning the switch on never sends these: a
person reviews them and requeues (`Outbox.requeue`).

Cancellation. A row can carry the entity it acts for (`entity=`). Cancelling
the entity cancels its PENDING rows (`Outbox.cancel_for`) and reports the
IN_FLIGHT ones, which can no longer be stopped. A kind's `pre_check` runs
under the row lock just before the effect and may skip it (the row ends
CANCELLED) when the entity's state no longer wants it.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError, NotFound
from app.platform.guard import Suppressed

logger = logging.getLogger(__name__)

PENDING, IN_FLIGHT, DONE, FAILED = "PENDING", "IN_FLIGHT", "DONE", "FAILED"
CANCELLED, SUPPRESSED = "CANCELLED", "SUPPRESSED"
# What a person may send again.
REQUEUEABLE = frozenset({FAILED, SUPPRESSED})


class NotAccepted(Exception):
    """The remote side did not take the message (connection failed, or it was
    refused before the transfer completed). Nothing was delivered."""


class AcceptedThenFailed(Exception):
    """The remote side accepted the message; something after that failed.
    The message was delivered and must not be sent again."""


class OutcomeUnknown(Exception):
    """The effect was started but its result was never recorded."""


class OutboxRecordMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=PENDING, index=True)
    # The entity the effect acts for, so cancelling it reaches its rows.
    entity_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # Attempts in the current round; a manual requeue starts a new round.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # PENDING: when the row is due. IN_FLIGHT: when its lease ends.
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
Hook = Callable[[Any, BaseException, Any], Awaitable[None]]  # (row, exc, uow)
PreCheck = Callable[[dict[str, Any], Any], Awaitable[str | None]]  # (payload, uow) → skip reason


@dataclass(frozen=True)
class OutboxKind:
    effect: Effect
    policy: RetryPolicy = ONCE
    on_failed: Hook | None = None
    # Re-checks the entity's state (lock it) just before the effect; a reason
    # cancels the row instead.
    pre_check: PreCheck | None = None
    on_suppressed: Hook | None = None


def entity_ref(entity: Any) -> tuple[str, str]:
    """(type, id) for an ORM object, or a (type, id) pair as given."""
    if isinstance(entity, tuple):
        etype, eid = entity
        return str(etype), str(eid)
    ident = inspect(entity).mapper.primary_key_from_instance(entity)
    if any(i is None for i in ident):
        raise ValueError("flush the entity before queueing effects for it")
    return entity.__tablename__, ",".join(str(i) for i in ident)


@dataclass
class CancelResult:
    cancelled: list[int] = field(default_factory=list)
    in_flight: list[int] = field(default_factory=list)  # already started: cannot be stopped


class Outbox:
    def __init__(self, model: type) -> None:
        self.model = model

    def add(self, session, kind: str, payload: dict[str, Any], *, entity: Any = None) -> Any:
        """Queue an effect in the caller's transaction."""
        etype, eid = entity_ref(entity) if entity is not None else (None, None)
        row = self.model(kind=kind, payload=payload, status=PENDING, attempts=0, manual=False,
                         entity_type=etype, entity_id=eid)
        session.add(row)
        return row

    async def _locked(self, session, row_id: int) -> Any:
        row = (await session.execute(
            select(self.model).where(self.model.id == row_id).with_for_update()
        )).scalar_one_or_none()
        if row is None:
            raise NotFound("outbox", row_id)
        return row

    async def requeue(self, session, row_id: int) -> Any:
        """A person's retry of a FAILED or SUPPRESSED row: one new round under
        its policy."""
        row = await self._locked(session, row_id)
        if row.status not in REQUEUEABLE:
            raise BusinessError("outbox.not_requeueable", f"outbox row {row_id} is {row.status}", {"status": row.status})
        row.status = PENDING
        row.attempts = 0
        row.manual = True
        row.next_attempt_at = datetime.now(timezone.utc)
        row.finished_at = None
        return row

    async def cancel(self, session, row_id: int, reason: str = "cancelled") -> bool:
        """Cancel one row if it has not started. False when it is IN_FLIGHT or
        already finished."""
        row = await self._locked(session, row_id)
        return self._cancel_row(row, reason)

    async def cancel_for(self, session, entity: Any, reason: str = "cancelled") -> CancelResult:
        """Cancel every not-yet-started row of an entity, in the caller's
        transaction. Rows already IN_FLIGHT are reported, so the caller can
        refuse the cancellation (the effect may be happening now)."""
        etype, eid = entity_ref(entity)
        m = self.model
        rows = (await session.execute(
            select(m).where(m.entity_type == etype, m.entity_id == eid, m.status.in_((PENDING, IN_FLIGHT)))
            .order_by(m.id).with_for_update()
        )).scalars().all()
        out = CancelResult()
        for row in rows:
            if row.status == IN_FLIGHT:
                out.in_flight.append(row.id)
            elif self._cancel_row(row, reason):
                out.cancelled.append(row.id)
        return out

    @staticmethod
    def _cancel_row(row, reason: str) -> bool:
        if row.status != PENDING:
            return False
        row.status = CANCELLED
        row.finished_at = datetime.now(timezone.utc)
        row.last_error = reason
        return True


@dataclass
class OutboxSummary:
    done: int = 0
    retry_scheduled: int = 0
    failed: int = 0
    cancelled: int = 0
    suppressed: int = 0
    unknown: int = 0  # the outcome could not be recorded; the row recovers as FAILED

    @property
    def processed(self) -> int:
        return self.done + self.retry_scheduled + self.failed + self.cancelled + self.suppressed + self.unknown


@dataclass(frozen=True)
class _Claim:
    row_id: int
    kind: str
    payload: dict[str, Any]
    ctx: OutboxContext


class OutboxWorker:
    """Executes due rows one at a time. Claiming a row locks it
    (`FOR UPDATE SKIP LOCKED`, so two workers never take the same row) and
    commits it as IN_FLIGHT; the effect runs outside any transaction; the
    outcome is recorded in a new one."""

    def __init__(
        self,
        outbox: Outbox,
        kinds: Mapping[str, OutboxKind],
        uow_factory: Callable[..., Any],
        *,
        batch: int = 50,
        lease: timedelta = timedelta(minutes=15),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.outbox = outbox
        self.kinds = kinds
        self.uow_factory = uow_factory
        self.batch = batch
        self.lease = lease
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
        outcome, claim = await self._claim()
        if claim is None:
            return outcome
        kind = self.kinds[claim.kind]
        try:
            await kind.effect(claim.payload, claim.ctx)
        except asyncio.CancelledError:
            # Shutting down mid-call: whether it happened is unknown.
            await self._record(claim, OutcomeUnknown("interrupted during the call"))
            raise
        except Exception as e:  # noqa: BLE001 — every failure is recorded on the row
            return await self._record(claim, e)
        return await self._record(claim, None)

    # ── phase 1: claim ───────────────────────────────────────────────────────
    async def _claim(self) -> tuple[str | None, _Claim | None]:
        m = self.outbox.model
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            now = self.clock()
            row = (await uow.session.execute(
                select(m)
                .where(m.status.in_((PENDING, IN_FLIGHT)), m.next_attempt_at <= now)
                .order_by(m.next_attempt_at, m.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )).scalar_one_or_none()
            if row is None:
                return None, None
            kind = self.kinds.get(row.kind)
            if row.status == IN_FLIGHT:
                # The lease passed without an outcome: never run it again.
                outcome = await self._fail(uow, row, OutcomeUnknown(
                    "the effect started but its result was never recorded"), kind, now)
            elif kind is None:
                row.attempts += 1
                outcome = await self._fail(uow, row, LookupError(f"no handler for outbox kind {row.kind!r}"), None, now)
            else:
                skip = await kind.pre_check(row.payload, uow) if kind.pre_check is not None else None
                if skip is not None:
                    Outbox._cancel_row(row, skip)
                    outcome = "cancelled"
                else:
                    row.attempts += 1
                    row.status = IN_FLIGHT
                    row.next_attempt_at = now + self.lease
                    claim = _Claim(row.id, row.kind, dict(row.payload),
                                   OutboxContext(row_id=row.id, kind=row.kind, attempt=row.attempts, manual=row.manual))
                    await uow.commit()
                    return None, claim
            await uow.commit()
            return outcome, None

    # ── phase 2: record the outcome ──────────────────────────────────────────
    async def _record(self, claim: _Claim, exc: BaseException | None) -> str:
        try:
            async with self.uow_factory(SYSTEM_ACTOR) as uow:
                row = await self.outbox._locked(uow.session, claim.row_id)
                if row.status != IN_FLIGHT:
                    # Recovered as FAILED (lease passed) meanwhile; keep that,
                    # but note what the call actually did.
                    logger.warning("outbox %s (%s) outcome %r arrived after the row became %s",
                                   row.id, row.kind, exc, row.status)
                    row.last_error = f"{row.last_error or ''}; late outcome: {_describe(exc) if exc else 'succeeded'}"
                    await uow.commit()
                    return "unknown"
                outcome = await self._apply(uow, row, exc, self.kinds[claim.kind], self.clock())
                await uow.commit()
                return outcome
        except Exception:  # noqa: BLE001 — the row stays IN_FLIGHT and recovers as FAILED
            logger.exception("outbox %s (%s): could not record the outcome", claim.row_id, claim.kind)
            return "unknown"

    async def _apply(self, uow, row, exc: BaseException | None, kind: OutboxKind, now: datetime) -> str:
        if exc is None:
            return self._done(row, now)
        if isinstance(exc, AcceptedThenFailed):
            # Delivered: never resend. The error is kept for the log only.
            logger.warning("outbox %s (%s) delivered, later step failed: %s", row.id, row.kind, exc)
            row.last_error = f"after delivery: {_describe(exc)}"
            return self._done(row, now)
        if isinstance(exc, Suppressed):
            row.status = SUPPRESSED
            row.finished_at = now
            row.last_error = str(exc)
            await uow.session.flush()
            await _hook(uow, kind.on_suppressed, row, exc)
            return "suppressed"
        if not isinstance(exc, OutcomeUnknown) and kind.policy.should_retry(exc, row.attempts):
            row.status = PENDING
            row.last_error = _describe(exc)
            row.next_attempt_at = now + kind.policy.delay(row.attempts)
            logger.info("outbox %s (%s) attempt %s not accepted; retry at %s",
                        row.id, row.kind, row.attempts, row.next_attempt_at)
            return "retry_scheduled"
        return await self._fail(uow, row, exc, kind, now)

    @staticmethod
    def _done(row, now: datetime) -> str:
        row.status = DONE
        row.finished_at = now
        return "done"

    async def _fail(self, uow, row, exc: BaseException, kind: OutboxKind | None, now: datetime) -> str:
        row.status = FAILED
        row.finished_at = now
        row.last_error = _describe(exc)
        logger.warning("outbox %s (%s) FAILED after %s attempt(s): %s", row.id, row.kind, row.attempts, exc)
        await uow.session.flush()  # FAILED is written before the hook, which cannot undo it
        await _hook(uow, kind.on_failed if kind else None, row, exc)
        return "failed"


async def _hook(uow, hook: Hook | None, row, exc: BaseException) -> None:
    """Runs a business hook in a SAVEPOINT: if it raises, its own writes are
    undone, the row's status stays."""
    if hook is None:
        return
    try:
        async with uow.session.begin_nested():
            await hook(row, exc, uow)
    except Exception:  # noqa: BLE001
        logger.exception("outbox %s (%s): hook %s failed", row.id, row.kind, getattr(hook, "__name__", hook))


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"
