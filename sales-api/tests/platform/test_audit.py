import pytest
from sqlalchemy import select

from app.platform.actors import SYSTEM_ACTOR
from tests.platform.conftest import AuditRow, Widget


async def test_create_update_record_actor_and_changes(uow_factory, person):
    async with uow_factory(person, reason="initial load") as uow:
        w = Widget(code="A", name="Alpha")
        uow.session.add(w)
        await uow.commit()
    assert (w.created_by, w.created_by_name) == (person.user_id, "Pat Person")
    async with uow_factory(SYSTEM_ACTOR) as uow:
        w2 = await uow.session.get(Widget, w.id)
        w2.name = "Alpha 2"
        await uow.commit()
    assert w2.updated_by is None and w2.updated_by_name == "SYSTEM" and w2.version == 2
    async with uow_factory(SYSTEM_ACTOR) as uow:
        rows = (await uow.session.execute(select(AuditRow).order_by(AuditRow.id))).scalars().all()
    assert [(r.action, r.entity, r.entity_id) for r in rows] == [
        ("create", "pt_widgets", str(w.id)), ("update", "pt_widgets", str(w.id)),
    ]
    assert rows[0].user_id == person.user_id and rows[0].reason == "initial load"
    assert rows[0].after["code"] == "A" and rows[0].before is None
    assert rows[1].before == {"name": "Alpha"} and rows[1].after == {"name": "Alpha 2"}
    assert rows[1].user_id is None and rows[1].user_name == "SYSTEM"


async def test_unchanged_flush_writes_no_audit_row(uow_factory, person):
    async with uow_factory(person) as uow:
        uow.session.add(Widget(code="A", name="Alpha"))
        await uow.commit()
    async with uow_factory(person) as uow:
        w = (await uow.session.execute(select(Widget))).scalar_one()
        w.name = "Alpha"  # same value
        await uow.commit()
        n = len((await uow.session.execute(select(AuditRow))).scalars().all())
    assert n == 1


async def test_flush_without_actor_is_refused(session_factory):
    from app.platform.audit import AuditTrail
    async with session_factory() as s:
        AuditTrail(AuditRow).attach(s.sync_session)
        s.add(Widget(code="A", name="Alpha"))
        with pytest.raises(RuntimeError, match="without an actor"):
            await s.flush()


async def test_rollback_leaves_no_audit_row(uow_factory, person):
    with pytest.raises(ZeroDivisionError):
        async with uow_factory(person) as uow:
            uow.session.add(Widget(code="A", name="Alpha"))
            await uow.session.flush()
            1 / 0
    async with uow_factory(person) as uow:
        assert (await uow.session.execute(select(AuditRow))).scalars().all() == []
        assert (await uow.session.execute(select(Widget))).scalars().all() == []
