"""Retry policy per kind (design §13.7, product owner 2026-10-27). Every
'sent once' case is paired with a case where a retry DOES happen, so a worker
that never retries cannot pass by accident."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.platform.actors import SYSTEM_ACTOR
from app.platform.gateways import ExternalEmail, FakeEmail
from app.platform.outbox import (
    DONE, FAILED, ONCE, PENDING, AcceptedThenFailed, NotAccepted, Outbox, OutboxKind, OutboxWorker,
    retry_while_not_accepted,
)
from tests.platform.conftest import OutboxRow

OUTBOX = Outbox(OutboxRow)


class Clock:
    def __init__(self):
        # Rows are stamped with the database's now(); start just after it.
        self.now = datetime.now(timezone.utc) + timedelta(seconds=5)

    def __call__(self):
        return self.now


async def _queue(uow_factory, kind, payload=None):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        row = OUTBOX.add(uow.session, kind, payload or {"n": 1})
        await uow.session.flush()
        await uow.commit()
        return row.id


async def _row(uow_factory, row_id):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        return (await uow.session.execute(select(OutboxRow).where(OutboxRow.id == row_id))).scalar_one()


class RemoteWrite:
    """An API write that times out even though the remote side created it."""

    def __init__(self, fail_times=1):
        self.created: list[dict] = []
        self.calls = 0
        self.fail_times = fail_times

    async def __call__(self, payload, ctx):
        self.calls += 1
        if ctx.manual and any(c == payload for c in self.created):
            return  # manual retry reads remote state first and writes only what is missing
        self.created.append(payload)
        if self.calls <= self.fail_times:
            raise TimeoutError("read timed out")


async def test_api_write_timeout_is_attempted_once_and_raises_one_task(uow_factory):
    write, failed = RemoteWrite(), []

    async def on_failed(row, exc, uow):
        failed.append((row.id, type(exc).__name__))

    worker = OutboxWorker(OUTBOX, {"write": OutboxKind(write, ONCE, on_failed)}, uow_factory, clock=Clock())
    rid = await _queue(uow_factory, "write")
    s1 = await worker.run_once()
    s2 = await worker.run_once()  # nothing due: no automatic retry
    row = await _row(uow_factory, rid)
    assert (s1.failed, s2.processed) == (1, 0)
    assert write.calls == 1 and row.status == FAILED and row.attempts == 1
    assert failed == [(rid, "TimeoutError")]


async def test_manual_retry_finds_the_write_present_and_writes_nothing(uow_factory):
    write = RemoteWrite()
    worker = OutboxWorker(OUTBOX, {"write": OutboxKind(write, ONCE)}, uow_factory, clock=Clock())
    rid = await _queue(uow_factory, "write")
    await worker.run_once()
    async with uow_factory(SYSTEM_ACTOR) as uow:
        await OUTBOX.requeue(uow.session, rid)
        await uow.commit()
    s = await worker.run_once()
    row = await _row(uow_factory, rid)
    assert s.done == 1 and row.status == DONE and row.manual is True
    assert len(write.created) == 1  # one remote record, never a duplicate


async def test_manual_retry_of_a_missing_write_does_write(uow_factory):
    class Flaky:
        calls = 0

        async def __call__(self, payload, ctx):
            self.calls += 1
            if not ctx.manual:
                raise ConnectionError("down")

    eff = Flaky()
    worker = OutboxWorker(OUTBOX, {"write": OutboxKind(eff, ONCE)}, uow_factory, clock=Clock())
    rid = await _queue(uow_factory, "write")
    await worker.run_once()
    async with uow_factory(SYSTEM_ACTOR) as uow:
        await OUTBOX.requeue(uow.session, rid)
        await uow.commit()
    await worker.run_once()
    assert eff.calls == 2 and (await _row(uow_factory, rid)).status == DONE


async def test_file_put_failure_is_attempted_once_and_waits_for_a_person(uow_factory):
    calls = []

    async def put(payload, ctx):
        calls.append(ctx.attempt)
        raise OSError("connection reset")

    clock = Clock()
    worker = OutboxWorker(OUTBOX, {"put": OutboxKind(put, ONCE)}, uow_factory, clock=clock)
    rid = await _queue(uow_factory, "put")
    await worker.run_once()
    clock.now += timedelta(days=1)
    await worker.run_once()
    assert calls == [1] and (await _row(uow_factory, rid)).status == FAILED


def _email_effect(gateway):
    async def send(payload, ctx):
        await gateway.send_external(ExternalEmail(to=("x@partner.example",), subject="s", body_text="b"))
    return send


async def test_email_not_accepted_is_retried_up_to_the_count(uow_factory):
    def refuse(n):
        raise NotAccepted("connection refused")

    gw, clock = FakeEmail(script=refuse), Clock()
    kinds = {"email": OutboxKind(_email_effect(gw), retry_while_not_accepted(3, (60,)))}
    worker = OutboxWorker(OUTBOX, kinds, uow_factory, clock=clock)
    rid = await _queue(uow_factory, "email")
    for _ in range(5):
        await worker.run_once()
        clock.now += timedelta(seconds=61)
    row = await _row(uow_factory, rid)
    assert gw.attempts == 3 and row.status == FAILED and gw.sent == []


async def test_email_not_accepted_then_accepted_is_sent_once(uow_factory):
    def first_refused(n):
        if n == 1:
            raise NotAccepted("421 try later")

    gw, clock = FakeEmail(script=first_refused), Clock()
    kinds = {"email": OutboxKind(_email_effect(gw), retry_while_not_accepted(3, (60,)))}
    worker = OutboxWorker(OUTBOX, kinds, uow_factory, clock=clock)
    rid = await _queue(uow_factory, "email")
    s1 = await worker.run_once()
    assert s1.retry_scheduled == 1 and (await _row(uow_factory, rid)).status == PENDING
    assert (await worker.run_once()).processed == 0  # back-off not elapsed
    clock.now += timedelta(seconds=61)
    await worker.run_once()
    assert gw.attempts == 2 and len(gw.sent) == 1 and (await _row(uow_factory, rid)).status == DONE


async def test_email_accepted_by_server_is_never_resent(uow_factory):
    def accepted_then_broken(n):
        raise AcceptedThenFailed("250 OK, then QUIT failed")

    gw, clock = FakeEmail(script=accepted_then_broken), Clock()
    kinds = {"email": OutboxKind(_email_effect(gw), retry_while_not_accepted(3, (60,)))}
    worker = OutboxWorker(OUTBOX, kinds, uow_factory, clock=clock)
    rid = await _queue(uow_factory, "email")
    for _ in range(3):
        await worker.run_once()
        clock.now += timedelta(seconds=61)
    row = await _row(uow_factory, rid)
    assert gw.attempts == 1 and row.status == DONE and "after delivery" in row.last_error


async def test_other_email_errors_are_not_retried(uow_factory):
    def boom(n):
        raise ValueError("bad address")

    gw, clock = FakeEmail(script=boom), Clock()
    kinds = {"email": OutboxKind(_email_effect(gw), retry_while_not_accepted(3, (60,)))}
    worker = OutboxWorker(OUTBOX, kinds, uow_factory, clock=clock)
    rid = await _queue(uow_factory, "email")
    for _ in range(3):
        await worker.run_once()
        clock.now += timedelta(seconds=61)
    assert gw.attempts == 1 and (await _row(uow_factory, rid)).status == FAILED


async def test_unknown_kind_fails_the_row(uow_factory):
    worker = OutboxWorker(OUTBOX, {}, uow_factory, clock=Clock())
    rid = await _queue(uow_factory, "nobody")
    await worker.run_once()
    assert (await _row(uow_factory, rid)).status == FAILED


async def test_rolled_back_business_change_leaves_no_outbox_row(uow_factory):
    try:
        async with uow_factory(SYSTEM_ACTOR) as uow:
            OUTBOX.add(uow.session, "write", {})
            await uow.session.flush()
            raise RuntimeError("business rule failed")
    except RuntimeError:
        pass
    async with uow_factory(SYSTEM_ACTOR) as uow:
        assert (await uow.session.execute(select(OutboxRow))).scalars().all() == []
