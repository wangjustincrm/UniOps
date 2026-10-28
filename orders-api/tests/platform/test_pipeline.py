import asyncio

import pytest
from sqlalchemy import select

from app.platform.events import DomainEvent, HandlerRegistry
from app.platform.pipeline import CycleContext, StepResult, isolated, run_steps
from app.platform.uow import UnitOfWorkFactory
from app.platform.audit import AuditTrail
from tests.platform.conftest import AuditRow, Widget


async def test_good_unit_commits_bad_unit_rolls_back_and_is_reported(uow_factory, person):
    logged = []

    async def log(outcome):
        logged.append(outcome)

    async def good(uow):
        uow.session.add(Widget(code="G", name="good"))
        return "ok"

    async def bad(uow):
        uow.session.add(Widget(code="B", name="bad"))
        await uow.session.flush()
        raise ValueError("unit failed")

    o1 = await isolated("good", good, uow_factory=uow_factory, log=log, actor=person)
    o2 = await isolated("bad", bad, uow_factory=uow_factory, log=log, actor=person, context={"doc": 7})
    assert o1.ok and o1.result == "ok"
    assert not o2.ok and o2.error_type == "ValueError" and o2.context == {"doc": 7} and "unit failed" in o2.trace
    assert logged == [o2]
    async with uow_factory(person) as uow:
        assert [w.code for w in (await uow.session.execute(select(Widget))).scalars()] == ["G"]


async def test_failing_logger_does_not_escape(uow_factory, person):
    async def log(outcome):
        raise RuntimeError("log table down")

    async def bad(uow):
        raise ValueError("x")

    out = await isolated("u", bad, uow_factory=uow_factory, log=log, actor=person)
    assert not out.ok


async def test_cancellation_propagates(uow_factory, person):
    async def cancelled(uow):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await isolated("u", cancelled, uow_factory=uow_factory, log=lambda o: None, actor=person)


async def test_steps_run_in_order():
    order = []

    class S:
        def __init__(self, name):
            self.name = name

        async def run(self, ctx):
            order.append(self.name)
            ctx.data[self.name] = True
            return StepResult(self.name)

    ctx = await run_steps([S("fetch"), S("apply")], CycleContext(run=None))
    assert order == ["fetch", "apply"] and ctx.failed == 0 and ctx.data == {"fetch": True, "apply": True}


class Raised(DomainEvent):
    pass


async def test_in_transaction_handlers_run_before_commit_and_roll_back_with_it(session_factory, person):
    handlers = HandlerRegistry()

    codes = iter(["H", "H2"])

    async def handler(event, uow):
        uow.session.add(Widget(code=next(codes), name="from handler"))

    handlers.on(Raised, handler)
    factory = UnitOfWorkFactory(session_factory, handlers=handlers, audit=AuditTrail(AuditRow))
    async with factory(person) as uow:
        uow.events.raise_(Raised(actor=person))
        await uow.commit()
    with pytest.raises(RuntimeError):
        async with factory(person) as uow:
            uow.events.raise_(Raised(actor=person))
            await handlers.dispatch(uow.events, uow)
            await uow.session.flush()
            raise RuntimeError("after the handler")
    async with factory(person) as uow:
        assert [w.code for w in (await uow.session.execute(select(Widget))).scalars()] == ["H"]


async def test_endless_event_loop_is_stopped(session_factory, person):
    handlers = HandlerRegistry()

    async def again(event, uow):
        uow.events.raise_(Raised(actor=person))

    handlers.on(Raised, again)
    factory = UnitOfWorkFactory(session_factory, handlers=handlers)
    async with factory(person) as uow:
        uow.events.raise_(Raised(actor=person))
        with pytest.raises(RuntimeError, match="still pending"):
            await uow.commit()
