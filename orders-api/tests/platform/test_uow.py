"""The request-scoped unit of work commits only when told to (design §13.4),
unlike get_session, which commits at the end of every request."""
import uuid

from sqlalchemy import select

from app.core.deps import make_get_uow
from app.platform.actors import SYSTEM_ACTOR
from app.platform.uow import UnitOfWorkFactory
from tests.platform.conftest import Note

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
