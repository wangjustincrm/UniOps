"""A record's parts move its version (platform/parts.py, ≈ ABP): made, changed in a column of their own, deleted — once per root and
flush, never for a root made in the same transaction; the root locked first. Real sessions on the test database."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import pytest
import sqlalchemy as sa
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ForeignKey, String, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.exc import StaleDataError

from app.platform import parts
from app.platform.bulk_actions import Action, ActionIn, ActionRegistry, run_action
from app.platform.auditing import begin_scope
from app.platform.clock import ClockOptions, FixedClock
from app.platform.commands import lock
from app.platform.entities import Audited, Entity, Versioned
from app.platform.errors import install_error_handlers
from app.platform.options import configure, restore, snapshot
from app.platform.parts import PartOf
from app.platform.routes import Router
from tests import root_locks
from tests.platform.conftest import AuditRow, PTBase, Widget, WidgetRepository, actor_with


class Box(Audited, Versioned, PTBase):
    __tablename__ = "pt_boxes"
    code: Mapped[str] = mapped_column(String(20), unique=True)
    note: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Piece(Audited, PTBase):
    __tablename__ = "pt_pieces"
    __part_of__ = PartOf(Box, "box_id")
    box_id: Mapped[int] = mapped_column(ForeignKey("pt_boxes.id"), nullable=False)
    n: Mapped[int] = mapped_column(default=0)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class Plain(Entity, PTBase):
    """Not audited, names its root by its code, and may be without one in the table."""
    __tablename__ = "pt_plains"
    __part_of__ = PartOf(Box, "box_code", "code")
    box_code: Mapped[str | None] = mapped_column(ForeignKey("pt_boxes.code"), nullable=True)
    n: Mapped[int] = mapped_column(default=0)


@pytest.fixture
def anyhow(monkeypatch):
    """The rule itself, without the tests' check of the lock."""
    monkeypatch.setattr(parts, "check_locked", None)


@pytest.fixture
def unit(uow_factory, person):
    return lambda who=None: uow_factory(who or person)


async def box(unit, pieces=1, code="B1"):
    async with unit() as u:
        b = Box(code=code)
        u.session.add(b)
        await u.session.flush()
        ps = [Piece(box_id=b.id, n=i) for i in range(pieces)]
        u.session.add_all(ps)
        u.session.add(Plain(box_code=code, n=0))
        await u.commit()
        return b.id, [p.id for p in ps]


async def version(unit, id):
    async with unit() as u:
        return (await u.session.execute(select(Box.version).where(Box.id == id))).scalar_one()


async def row(unit, id):
    async with unit() as u:
        return await u.session.get(Box, id)


async def locked(u, id):
    return await u.session.get(Box, id, with_for_update=True)


# ── made, changed, deleted ───────────────────────────────────────────────────

async def test_a_new_record_with_its_parts_is_version_1(unit):
    id, _ = await box(unit, 3)
    assert await version(unit, id) == 1


async def test_a_part_made_changed_or_deleted_moves_it_by_one_and_says_who_and_when(unit, uow_factory):
    id, [pid] = await box(unit)
    other = actor_with("pt.read", name="Olga Other")
    before = datetime.now(timezone.utc) - timedelta(seconds=1)

    async def made(s):
        s.add(Piece(box_id=id, n=9))

    async def changed(s):
        (await s.get(Piece, pid)).n = 77

    async def deleted(s):
        await s.delete(await s.get(Piece, pid))
    for n, act in enumerate((made, changed, deleted), start=2):
        async with uow_factory(other) as u:
            await locked(u, id)
            await act(u.session)
            await u.commit()
        b = await row(unit, id)
        assert (b.version, b.updated_by, b.updated_by_name) == (n, other.user_id, "Olga Other") and b.updated_at > before


async def test_a_part_that_names_its_root_by_another_key(unit):
    id, _ = await box(unit)
    async with unit() as u:
        await u.session.execute(select(Box).where(Box.code == "B1").with_for_update())
        (await u.session.execute(select(Plain))).scalar_one().n = 5
        await u.commit()
    assert await version(unit, id) == 2


async def test_what_is_no_change_does_not_move_it(unit):
    id, [pid] = await box(unit)
    async with unit() as u:
        await locked(u, id)
        p = await u.session.get(Piece, pid)
        p.n, p.data = 0, {}                                      # what they were
        p.n = 5
        p.n = 0                                                  # there and back
        p.updated_at, p.updated_by_name = datetime.now(timezone.utc), "x"  # bookkeeping alone
        await u.commit()
    assert await version(unit, id) == 1


async def test_once_per_flush_however_many_parts_and_its_own_change_with_it(unit):
    id, pids = await box(unit, 3)
    async with unit() as u:
        b = await locked(u, id)
        for p in [await u.session.get(Piece, pid) for pid in pids]:  # loaded first: no flush in between
            p.n += 100
        u.session.add(Piece(box_id=id, n=1))
        b.note = "and a change of its own"
        await u.session.flush()
        assert b.version == 2
        (await u.session.get(Piece, pids[0])).n += 1
        await u.session.flush()
        assert b.version == 3  # another flush: another step; the answer is read after the last
        await u.commit()
    assert await version(unit, id) == 3


async def test_the_audit_has_the_part_and_not_an_empty_row_of_the_root(unit):
    id, [pid] = await box(unit)
    async with unit() as u:
        await locked(u, id)
        (await u.session.get(Piece, pid)).n = 8
        await u.commit()
    async with unit() as u:
        rows = (await u.session.execute(select(AuditRow.entity, AuditRow.action).order_by(AuditRow.id))).all()
    assert rows[-1] == ("pt_pieces", "update") and ("pt_boxes", "update") not in [tuple(r) for r in rows]


async def test_a_part_moved_to_another_root_moves_both(unit):
    id, [pid] = await box(unit)
    to, _ = await box(unit, 0, "B2")
    async with unit() as u:
        await locked(u, id)
        await locked(u, to)
        (await u.session.get(Piece, pid)).box_id = to
        await u.commit()
    assert (await version(unit, id), await version(unit, to)) == (2, 2)


async def test_a_root_deleted_with_its_parts_and_a_part_without_a_root(unit, anyhow):
    id, [pid] = await box(unit)
    async with unit() as u:
        await u.session.delete(await u.session.get(Piece, pid))
        await u.session.delete((await u.session.execute(select(Plain))).scalar_one())
        await u.session.flush()
        await u.session.delete(await u.session.get(Box, id))
        await u.commit()
    async with unit() as u:
        assert (await u.session.execute(select(Box))).scalars().all() == []
        u.session.add(Plain(n=1))
        with pytest.raises(RuntimeError, match="a Plain without its Box"):
            await u.session.flush()
        await u.rollback()


async def test_only_within_its_transaction_is_a_root_new(unit):
    async with unit() as u:
        b = Box(code="B9")
        u.session.add(b)
        await u.session.flush()
        u.session.add(Piece(box_id=b.id))
        await u.session.flush()
        await u.session.commit()
        assert b.version == 1
        await locked(u, b.id)
        u.session.add(Piece(box_id=b.id))  # the same session, the next transaction
        await u.session.flush()
        assert b.version == 2
        await u.session.commit()


async def test_a_root_made_in_a_savepoint_of_the_transaction_is_new_too(unit):
    async with unit() as u:
        async with u.session.begin_nested():
            b = Box(code="B8")
            u.session.add(b)
            await u.session.flush()
        u.session.add(Piece(box_id=b.id))
        await u.session.flush()
        assert b.version == 1


async def test_twice_in_the_same_instant_by_the_same_person(unit):
    id, [pid] = await box(unit)
    before = snapshot()
    configure(ClockOptions(provider=FixedClock(datetime(2026, 11, 25, 12, tzinfo=timezone.utc))))
    try:
        for n in (5, 6):
            async with unit() as u:
                await locked(u, id)
                (await u.session.get(Piece, pid)).n = n
                await u.commit()
    finally:
        restore(before)
    assert await version(unit, id) == 3


# ── the root, not loaded, not locked, changed meanwhile ──────────────────────

async def test_a_root_that_is_not_loaded_is_loaded_locked_and_moved(unit, anyhow):
    id, [pid] = await box(unit)
    async with unit() as u, unit() as w:
        (await u.session.get(Piece, pid)).n = 4
        await u.session.flush()
        held = (await w.session.execute(select(Box.id).where(Box.id == id).with_for_update(skip_locked=True))).scalar()
        assert held is None  # locked
        await u.commit()
    assert await version(unit, id) == 2


async def test_a_root_not_loaded_and_held_by_another_is_waited_for_and_read_as_it_then_is(unit, anyhow):
    id, [pid] = await box(unit)

    async def late():
        async with unit() as u:
            (await u.session.get(Piece, pid)).n = 4
            await u.commit()
    async with unit() as holder:
        (await locked(holder, id)).note = "theirs"
        await holder.session.flush()
        task = asyncio.create_task(late())
        await asyncio.sleep(0.4)
        assert not task.done()
        await holder.commit()
    await asyncio.wait_for(task, 10)  # not refused: it had seen nothing of it
    assert await version(unit, id) == 3


async def test_a_root_seen_before_someone_else_moved_it_is_refused(unit, anyhow):
    id, [pid] = await box(unit)
    async with unit() as late:
        seen = await late.session.get(Box, id)
        assert seen.version == 1
        async with unit() as u:
            await locked(u, id)
            (await u.session.get(Piece, pid)).n = 1
            await u.commit()
        late.session.add(Piece(box_id=id, n=2))
        with pytest.raises(StaleDataError):
            await late.session.flush()
        await late.rollback()
    assert await version(unit, id) == 2


async def test_the_tests_check_refuses_a_part_written_before_its_root_s_lock(unit):
    id, [pid] = await box(unit)

    async def write(*first, then=None):
        async with unit() as u:
            for act in first:
                await act(u.session)
            (await u.session.get(Piece, pid)).n += 1
            await u.session.flush()
            await u.commit()
            if then:
                (await u.session.get(Piece, pid)).n += 1
                await u.session.flush()
    get_lock = lambda s: s.get(Box, id, with_for_update=True)  # noqa: E731
    select_lock = lambda s: s.execute(select(Box).where(Box.id == id).with_for_update())  # noqa: E731

    async def refresh_lock(s):
        await s.refresh(await s.get(Box, id), with_for_update=True)

    async def update_first(s):
        (await s.get(Box, id)).note = "its own change, flushed"
        await s.flush()

    async def read_only(s):
        await s.get(Box, id)

    async def another(s):
        s.add(Box(code="B7"))
        await s.flush()
        await s.execute(select(Box).where(Box.code == "B7").with_for_update())
    for first in (get_lock, select_lock, refresh_lock, update_first):
        await write(first)
    for first in ((), (read_only,), (another,)):
        with pytest.raises(root_locks.NotLocked, match=f"a part of Box \\({id},\\)"):
            await write(*first)
    with pytest.raises(root_locks.NotLocked):  # the next transaction holds nothing of the last
        await write(get_lock, then=True)

    async def same_value(s):
        b = await s.get(Box, id)
        b.note = b.note  # in dirty, and no UPDATE
        b.note, b.code = "x", "x"
        b.note, b.code = "its own change, flushed", "B1"
        await s.flush()
    with pytest.raises(root_locks.NotLocked):
        await write(same_value)
    assert len(root_locks.violations) == 5
    root_locks.violations.clear()


async def test_a_refusal_a_job_swallows_still_fails_its_test(unit, uow_factory):
    from app.platform.pipeline import isolated
    id, [pid] = await box(unit)

    async def job(u):
        (await u.session.get(Piece, pid)).n = 9

    async def log(outcome):
        pass
    out = await isolated("a job", job, uow_factory=uow_factory, log=log)
    assert out.ok is False and root_locks.violations == [f"Box ({id},)"]  # the fixture would fail the test here
    root_locks.violations.clear()


def test_every_test_ends_with_no_violation(request):
    """The fixture is on for every test, and fails one that leaves a violation."""
    name = "_parts_written_under_their_root_s_lock"
    assert name in request.fixturenames
    [d] = request._fixturemanager.getfixturedefs(name, request.node)
    run = d.func()
    next(run)
    root_locks.violations.append("Box (1,)")
    with pytest.raises(AssertionError):
        next(run)
    root_locks.violations.clear()


async def test_a_stale_record_in_an_action_is_kept_in_the_log(uow_factory, person, caplog):
    async with uow_factory(person) as u:
        w = Widget(code="W2", name="one", version=1)
        u.session.add(w)
        await u.session.flush()
        id = w.id
        await u.commit()

    async def run(uow, w, data, ctx=None):  # noqa: ARG001
        raise StaleDataError("UPDATE statement on table 'pt_x' expected to update 1 row(s); 0 were matched.")
    async with uow_factory(person) as u:
        await run_action(u, ActionRegistry(WidgetRepository, [Action("x", label="X", permission="pt.operate", run=run)]), "x", ActionIn(ids={id: 1}))
    assert any("pt_x" in r.getMessage() and r.exc_info and r.exc_info[0] is StaleDataError for r in caplog.records)


# ── locked and read again ────────────────────────────────────────────────────

async def test_lock_reads_again_what_another_changed(unit):
    id, _ = await box(unit)
    async with unit() as u:
        b = await u.session.get(Box, id)
        async with unit() as w:
            (await locked(w, id)).note = "theirs"
            await w.commit()
        assert b.note is None and await lock(u.session, Box, id) is b and (b.note, b.version) == ("theirs", 2)


@pytest.mark.parametrize("pending", ["changed", "deleted"])
async def test_lock_refuses_a_row_with_changes_not_yet_written(unit, pending):
    id, _ = await box(unit, 0)
    async with unit() as u:
        b = await u.session.get(Box, id)
        if pending == "changed":
            b.note = "typed"
        else:
            await u.session.delete(b)
        with pytest.raises(RuntimeError, match=f"Box {id} has changes not yet written"):
            await lock(u.session, Box, id)
        assert b.note == ("typed" if pending == "changed" else None)  # nothing lost
        if pending == "changed":
            b.note = None  # back to what it was: nothing pending
            assert await lock(u.session, Box, id) is b
            b.note = "after its lock"
            await u.session.flush()
            assert (await lock(u.session, Box, id)).note == "after its lock"  # written: no longer pending


async def test_another_row_s_pending_changes_do_not_matter(unit):
    id, [pid] = await box(unit)
    async with unit() as u:
        (await u.session.get(Piece, pid)).n = 3
        await lock(u.session, Box, id)
        await u.session.commit()
    async with unit() as u:
        assert (await u.session.get(Piece, pid)).n == 3


# ── what a person is told ────────────────────────────────────────────────────

async def test_a_stale_record_anywhere_in_a_request_is_409_and_keeps_nothing(unit, uow_factory, person, anyhow, caplog):
    id, [pid] = await box(unit)
    app = FastAPI()
    install_error_handlers(app)

    async def get_uow():
        async with uow_factory(person) as u:
            yield u
    r, held = Router(), []

    @r.post("/w")
    async def write(u: Annotated[Any, Depends(get_uow)], flush: bool = True):
        held.append(await u.session.get(Box, id))  # in memory, as this request saw it
        async with unit() as other:
            await locked(other, id)
            (await other.session.get(Piece, pid)).n += 1
            await other.commit()
        u.session.add(Piece(box_id=id, n=2))
        if flush:
            await u.session.flush()
        return {"ok": True}
    app.include_router(r)
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
        for flush in ("true", "false"):  # in the endpoint, and at the commit
            caplog.clear()
            async with begin_scope("request", "test") as scope:
                a = await c.post("/w", params={"flush": flush})
            # what it was is kept: the table and the rows, in the log with its stack and in the request's record
            [kept] = scope.log.exceptions if isinstance(scope.log.exceptions, list) else [scope.log.exceptions]
            assert "pt_boxes" in str(kept) and "0 were matched" in str(kept) and "entity.stale" in str(kept)
            assert any("pt_boxes" in r.getMessage() and r.exc_info and r.exc_info[0] is StaleDataError for r in caplog.records)
            assert "pt_boxes" not in a.text  # not for the person
            assert (a.status_code, a.json()["error"]["code"]) == (409, "entity.stale") and "reload" in a.json()["error"]["message"]
    async with unit() as u:
        assert (await u.session.execute(select(sa.func.count(Piece.id)))).scalar_one() == 1


async def test_a_stale_record_of_many_is_that_record_s_failure(uow_factory, person):
    async with uow_factory(person) as u:
        w = Widget(code="W1", name="one", version=1)
        u.session.add(w)
        await u.session.flush()
        id = w.id
        await u.commit()

    async def run(uow, w, data, ctx=None):  # noqa: ARG001
        raise StaleDataError("UPDATE of another record it touched matched no row")
    reg = ActionRegistry(WidgetRepository, [Action("x", label="X", permission="pt.operate", run=run)])
    async with uow_factory(person) as u:
        out = await run_action(u, reg, "x", ActionIn(ids={id: 1}))
    assert [(r.status, r.code) for r in out.results] == [("failed", "entity.stale")]
