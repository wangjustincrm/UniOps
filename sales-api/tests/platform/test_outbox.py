"""Retry policy per kind (design §13.7, product owner 2026-10-27). Every
'sent once' case is paired with a case where a retry DOES happen, so a worker
that never retries cannot pass by accident."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.platform.actors import SYSTEM_ACTOR
from app.integrations.email import ExternalEmail, FakeEmail
from app.platform.outbox import (
    CANCELLED, DONE, FAILED, IN_FLIGHT, ONCE, PENDING, SUPPRESSED, AcceptedThenFailed, NotAccepted, Outbox,
    OutboxKind, OutboxWorker, retry_while_not_accepted,
)
from tests.platform.conftest import OutboxRow

OUTBOX = Outbox(OutboxRow)


class Clock:
    def __init__(self):
        # Rows are stamped with the database's now(); start just after it.
        self.now = datetime.now(timezone.utc) + timedelta(seconds=5)

    def __call__(self):
        return self.now


async def _queue(uow_factory, kind, payload=None, entity=None):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        row = OUTBOX.add(uow.session, kind, payload or {"n": 1}, entity=entity)
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


# ── at most once (review 2026-10-28) ─────────────────────────────────────────


class FailingCommits:
    """A uow factory whose Nth unit (1-based) cannot commit — the outcome of
    an effect that already ran is lost."""

    def __init__(self, inner, fail_units):
        self.inner, self.fail_units, self.n = inner, set(fail_units), 0

    def __call__(self, actor, **kw):
        self.n += 1
        uow = self.inner(actor, **kw)
        if self.n in self.fail_units:
            async def broken():
                raise ConnectionError("connection lost during COMMIT")
            uow.commit = broken
        return uow


def _counting(calls):
    async def effect(payload, ctx):
        calls.append(ctx.attempt)
    return effect


async def test_effect_runs_once_when_its_outcome_cannot_be_committed(uow_factory):
    calls, failed, clock = [], [], Clock()

    async def on_failed(row, exc, uow):
        failed.append(type(exc).__name__)

    flaky = FailingCommits(uow_factory, fail_units={2})  # unit 1 claims, unit 2 records
    worker = OutboxWorker(OUTBOX, {"put": OutboxKind(_counting(calls), ONCE, on_failed)}, flaky, clock=clock)
    rid = await _queue(uow_factory, "put")
    s1 = await worker.run_once()
    row = await _row(uow_factory, rid)
    assert s1.unknown == 1 and row.status == IN_FLIGHT and row.attempts == 1
    assert (await worker.run_once()).processed == 0  # still within its lease: left alone
    clock.now += timedelta(minutes=16)
    s3 = await worker.run_once()
    row = await _row(uow_factory, rid)
    assert calls == [1], "the effect must not run a second time"
    assert s3.failed == 1 and row.status == FAILED and "OutcomeUnknown" in row.last_error
    assert failed == ["OutcomeUnknown"]


async def test_a_retrying_kind_is_not_resent_either_when_its_outcome_was_lost(uow_factory):
    gw, clock = FakeEmail(), Clock()
    kinds = {"email": OutboxKind(_email_effect(gw), retry_while_not_accepted(3, (60,)))}
    worker = OutboxWorker(OUTBOX, kinds, FailingCommits(uow_factory, {2}), clock=clock)
    rid = await _queue(uow_factory, "email")
    await worker.run_once()
    clock.now += timedelta(minutes=16)
    await worker.run_once()
    assert gw.attempts == 1 and (await _row(uow_factory, rid)).status == FAILED


async def test_a_raising_on_failed_does_not_undo_failed(uow_factory):
    calls = []

    async def boom(payload, ctx):
        calls.append(1)
        raise OSError("reset")

    async def on_failed(row, exc, uow):
        uow.session.add(OutboxRow(kind="marker", payload={}, status=PENDING, attempts=0, manual=False))
        await uow.session.flush()
        raise RuntimeError("task service down")

    clock = Clock()
    worker = OutboxWorker(OUTBOX, {"put": OutboxKind(boom, ONCE, on_failed)}, uow_factory, clock=clock)
    rid = await _queue(uow_factory, "put")
    s = await worker.run_once()
    clock.now += timedelta(days=1)
    await worker.run_once()
    row = await _row(uow_factory, rid)
    assert s.failed == 1 and calls == [1] and row.status == FAILED
    async with uow_factory(SYSTEM_ACTOR) as uow:
        kinds = (await uow.session.execute(select(OutboxRow.kind))).scalars().all()
    assert kinds == ["put"], "the hook's own writes are rolled back with its savepoint"


async def test_on_failed_writes_are_kept_when_it_succeeds(uow_factory):
    async def boom(payload, ctx):
        raise OSError("reset")

    async def on_failed(row, exc, uow):
        uow.session.add(OutboxRow(kind="marker", payload={}, status=DONE, attempts=0, manual=False))

    worker = OutboxWorker(OUTBOX, {"put": OutboxKind(boom, ONCE, on_failed)}, uow_factory, clock=Clock())
    await _queue(uow_factory, "put")
    await worker.run_once()
    async with uow_factory(SYSTEM_ACTOR) as uow:
        assert sorted((await uow.session.execute(select(OutboxRow.kind))).scalars().all()) == ["marker", "put"]


async def test_cancellation_during_the_call_is_recorded_as_unknown(uow_factory):
    import asyncio

    import pytest

    async def hang(payload, ctx):
        raise asyncio.CancelledError()

    worker = OutboxWorker(OUTBOX, {"put": OutboxKind(hang, ONCE)}, uow_factory, clock=Clock())
    rid = await _queue(uow_factory, "put")
    with pytest.raises(asyncio.CancelledError):
        await worker.run_once()
    row = await _row(uow_factory, rid)
    assert row.status == FAILED and "interrupted" in row.last_error


# ── cancellation (FR-OB-18 940 Generated → Cancelled; invoice Blocked) ───────


async def test_a_cancelled_940_put_never_runs(uow_factory):
    calls = []
    worker = OutboxWorker(OUTBOX, {"put": OutboxKind(_counting(calls), ONCE)}, uow_factory, clock=Clock())
    rid = await _queue(uow_factory, "put", entity=("orders", 42))
    other = await _queue(uow_factory, "put", entity=("orders", 43))
    async with uow_factory(SYSTEM_ACTOR) as uow:
        res = await OUTBOX.cancel_for(uow.session, ("orders", 42), "order cancelled")
        await uow.commit()
    assert res.cancelled == [rid] and res.in_flight == []
    s = await worker.run_once()
    row = await _row(uow_factory, rid)
    assert row.status == CANCELLED and row.last_error == "order cancelled"
    assert s.done == 1 and calls == [1] and (await _row(uow_factory, other)).status == DONE


async def test_cancel_reports_a_row_already_in_flight(uow_factory):
    worker = OutboxWorker(OUTBOX, {"put": OutboxKind(_counting([]), ONCE)}, FailingCommits(uow_factory, {2}),
                          clock=Clock())
    rid = await _queue(uow_factory, "put", entity=("orders", 7))
    await worker.run_once()  # claimed, outcome lost: IN_FLIGHT
    async with uow_factory(SYSTEM_ACTOR) as uow:
        res = await OUTBOX.cancel_for(uow.session, ("orders", 7))
        assert await OUTBOX.cancel(uow.session, rid) is False
        await uow.commit()
    assert res.cancelled == [] and res.in_flight == [rid]


async def test_pre_check_skips_an_effect_the_entity_no_longer_wants(uow_factory):
    calls, state = [], {"status": "Blocked"}

    async def still_queued(payload, uow):
        return None if state["status"] == "Queued" else f"invoice is {state['status']}"

    worker = OutboxWorker(OUTBOX, {"send": OutboxKind(_counting(calls), ONCE, pre_check=still_queued)},
                          uow_factory, clock=Clock())
    blocked = await _queue(uow_factory, "send")
    s = await worker.run_once()
    assert s.cancelled == 1 and calls == [] and (await _row(uow_factory, blocked)).last_error == "invoice is Blocked"
    state["status"] = "Queued"
    ok = await _queue(uow_factory, "send")
    await worker.run_once()
    assert calls == [1] and (await _row(uow_factory, ok)).status == DONE


# ── suppressed (product owner, 2026-10-28) ───────────────────────────────────


def _guarded_email(settings, inner):
    from app.platform.guard import GuardedGateway
    return GuardedGateway(inner, settings=settings, guarded={"send_external": "MAIL"})


async def test_suppressed_send_ends_suppressed_and_needs_a_person(uow_factory):
    from types import SimpleNamespace

    settings, inner, seen, clock = SimpleNamespace(MAIL=False), FakeEmail(), [], Clock()

    async def on_suppressed(row, exc, uow):
        seen.append(exc.refused.reason)

    kinds = {"email": OutboxKind(_email_effect(_guarded_email(settings, inner)), retry_while_not_accepted(3, (60,)),
                                 on_suppressed=on_suppressed)}
    worker = OutboxWorker(OUTBOX, kinds, uow_factory, clock=clock)
    rid = await _queue(uow_factory, "email")
    s = await worker.run_once()
    row = await _row(uow_factory, rid)
    assert s.suppressed == 1 and row.status == SUPPRESSED and seen == ["switch off"] and inner.sent == []

    settings.MAIL = True  # turning the switch on sends nothing by itself
    clock.now += timedelta(days=1)
    assert (await worker.run_once()).processed == 0 and inner.sent == []

    async with uow_factory(SYSTEM_ACTOR) as uow:
        await OUTBOX.requeue(uow.session, rid)  # a person reviewed it
        await uow.commit()
    await worker.run_once()
    assert len(inner.sent) == 1 and (await _row(uow_factory, rid)).status == DONE


async def test_requeued_suppressed_row_with_switch_still_off_is_suppressed_again(uow_factory):
    from types import SimpleNamespace

    settings, inner = SimpleNamespace(MAIL=False), FakeEmail()
    kinds = {"email": OutboxKind(_email_effect(_guarded_email(settings, inner)), ONCE)}
    worker = OutboxWorker(OUTBOX, kinds, uow_factory, clock=Clock())
    rid = await _queue(uow_factory, "email")
    await worker.run_once()
    async with uow_factory(SYSTEM_ACTOR) as uow:
        await OUTBOX.requeue(uow.session, rid)
        await uow.commit()
    s = await worker.run_once()
    row = await _row(uow_factory, rid)
    assert s.suppressed == 1 and s.done == 0 and row.status == SUPPRESSED and inner.sent == []


async def test_requeue_refuses_rows_that_are_not_failed_or_suppressed(uow_factory):
    import pytest

    from app.platform.errors import BusinessError

    worker = OutboxWorker(OUTBOX, {"put": OutboxKind(_counting([]), ONCE)}, uow_factory, clock=Clock())
    rid = await _queue(uow_factory, "put")
    await worker.run_once()
    async with uow_factory(SYSTEM_ACTOR) as uow:
        with pytest.raises(BusinessError) as e:
            await OUTBOX.requeue(uow.session, rid)
    assert e.value.code == "outbox.not_requeueable"
