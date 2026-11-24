"""The request-scoped unit of work commits only when told to (design §13.4),
unlike get_session, which commits at the end of every request."""
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.core.deps import make_get_uow
from app.platform.actors import SYSTEM_ACTOR
from app.platform.events import StatusChanged
from app.platform.uow import Savepoint, UnitOfWorkFactory
from tests.platform.conftest import Note, Widget, actor_with

PAYLOAD = {"sub": str(uuid.uuid4()), "full_name": "Pat Person", "type": "access"}


async def _titles(session_factory):
    async with UnitOfWorkFactory(session_factory)(SYSTEM_ACTOR) as uow:
        return sorted((await uow.session.execute(select(Note.title))).scalars().all())


async def _request(session_factory, *, commit: bool, fail: bool = False, title: str):
    dep = make_get_uow(UnitOfWorkFactory(session_factory))(PAYLOAD)
    uow = await dep.__anext__()
    assert uow.actor.name == "Pat Person"
    uow.session.add(Note(title=title))
    await uow.session.flush()
    if commit:
        await uow.commit()
    if fail:
        try:
            await dep.athrow(RuntimeError("endpoint failed"))
        except RuntimeError:
            pass
    else:
        try:
            await dep.__anext__()
        except StopAsyncIteration:
            pass


async def test_nothing_is_committed_unless_commit_is_called(session_factory):
    await _request(session_factory, commit=False, title="forgotten")
    await _request(session_factory, commit=False, fail=True, title="failed")
    assert await _titles(session_factory) == []


async def test_commit_is_kept(session_factory):
    await _request(session_factory, commit=True, title="kept")
    assert await _titles(session_factory) == ["kept"]


async def _raise(uow, new):
    uow.events.raise_(StatusChanged(actor=uow.actor, machine="m", entity="w", entity_id=1, old="a", new=new))


async def test_a_savepoint_s_writes_and_events_go_together(uow_factory):
    """Kept or undone, the two ways of using it alike (user 2026-11-24)."""
    async with uow_factory(actor_with("pt.read")) as u:
        async with Savepoint.of(u):
            u.session.add(Widget(code="KEPT", name="k"))
            await _raise(u, "kept")
        with pytest.raises(RuntimeError):
            async with Savepoint.of(u):
                u.session.add(Widget(code="GONE", name="g"))
                await u.session.flush()
                await _raise(u, "gone")
                raise RuntimeError("refused")
        sp = await Savepoint.begin(u)
        u.session.add(Widget(code="GONE2", name="g"))
        await _raise(u, "gone2")
        await sp.undo()
        assert len(u.events) == 1
        await u.commit()
        assert [e.new for e in u.events.dispatched] == ["kept"]
    async with uow_factory(actor_with("pt.read")) as u:
        assert (await u.session.execute(sa.select(Widget.code))).scalars().all() == ["KEPT"]


async def test_a_savepoint_refused_when_it_is_kept_is_undone_and_the_unit_goes_on(uow_factory):
    """The write is refused at keep()'s flush (a duplicate key): its writes and events are gone, the rest commits."""
    async with uow_factory(actor_with("pt.read")) as u:
        u.session.add(Widget(code="A", name="first"))
        await u.session.flush()
        with pytest.raises(sa.exc.IntegrityError):
            async with Savepoint.of(u):
                u.session.add(Widget(code="B", name="lost with it"))
                await _raise(u, "gone")
                u.session.add(Widget(code="A", name="again"))  # flushed only when it is kept
        u.session.add(Widget(code="C", name="after"))
        await _raise(u, "after")
        await u.commit()
        assert [e.new for e in u.events.dispatched] == ["after"]
    async with uow_factory(actor_with("pt.read")) as u:
        assert (await u.session.execute(sa.select(Widget.code).order_by(Widget.code))).scalars().all() == ["A", "C"]
