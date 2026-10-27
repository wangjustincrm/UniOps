"""Round 10, TC-04: an email is sent only once what it is about has been saved.

It used to be sent straight after the row was written, from a session of its own
that could not see the row until the caller committed. A web request usually
committed first; the scheduler, committing once at the end of a tick, never did
— so every expiry notice and toolbox chase was filed and never sent.
"""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.services import mail


@pytest.fixture
def spawned(monkeypatch):
    seen: list = []

    def fake_spawn(coro, *, name=None):
        coro.close()
        seen.append(name)
    monkeypatch.setattr(mail.background, "spawn", fake_spawn)
    return seen


async def test_nothing_is_sent_until_the_transaction_commits(test_engine, spawned):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = await mail.queue(db, to=["a@example.com"], subject="Hello", body="Body")
        assert spawned == [], "handed to the sender before the row could be read"
        await db.commit()
        assert spawned == [f"ehs-mail-{row.id}"]
        # Cleanup: the row was really written.
        await db.delete(row)
        await db.commit()


async def test_a_rolled_back_email_is_never_sent(test_engine, spawned):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await mail.queue(db, to=["a@example.com"], subject="Hello", body="Body")
        await db.rollback()
        # A later commit in the same session must not send the discarded one.
        await db.commit()
    assert spawned == []


async def test_the_sender_finds_the_row_once_committed(test_engine, monkeypatch, spawned):
    """The other half: `deliver`, in its own session, now sees what it is sent."""
    import app.db.session as session_mod
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(session_mod, "AsyncSessionLocal", factory)
    sent: list = []

    async def fake_send(msg, **kw):
        sent.append(msg["Subject"])

    import aiosmtplib
    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    monkeypatch.setattr(mail, "_smtp", lambda cfg: {
        "host": "smtp.example.com", "port": 587, "user": None, "password": None,
        "use_tls": True, "from_addr": "safety@example.com"})
    async with factory() as db:
        row = await mail.queue(db, to=["a@example.com"], subject="Committed", body="Body")
        await db.commit()
    assert await mail.deliver(row.id) is True
    assert sent == ["Committed"]
    async with factory() as db:
        await db.delete(await db.get(mail.Communication, row.id))
        await db.commit()


async def test_a_savepoint_neither_sends_nor_drops(test_engine, spawned):
    """★ Review (uniops-8b): the events fire for a SAVEPOINT too. Released, it
    used to send before the request committed; rolled back, it dropped mail
    the request then committed."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        row = await mail.queue(db, to=["a@example.com"], subject="Hello", body="Body")
        async with db.begin_nested():
            pass
        assert spawned == [], "sent on a savepoint's release"
        sp = await db.begin_nested()
        await sp.rollback()
        await db.commit()
        assert spawned == [f"ehs-mail-{row.id}"], "dropped by a savepoint's rollback"
        await db.delete(row)
        await db.commit()
