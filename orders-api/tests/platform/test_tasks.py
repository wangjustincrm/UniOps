import uuid

from sqlalchemy import select

from app.platform.actors import SYSTEM_ACTOR
from app.platform.tasks import ProblemTask, TaskService
from tests.platform.conftest import TASKS, Widget

OPERATOR = uuid.uuid4()


async def _assignee(session):
    return OPERATOR


SVC = TaskService(table=TASKS, prefix="PT", document_type="pt_module", assigned_role="pt_operator", assignee=_assignee)


async def _tasks(uow_factory):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        return (await uow.session.execute(select(TASKS).order_by(TASKS.c.created_at))).mappings().all()


async def test_one_open_task_per_problem_and_refresh(uow_factory):
    for i in range(3):  # the same problem every cycle
        async with uow_factory(SYSTEM_ACTOR) as uow:
            await SVC.raise_problem(uow.session, "sftp_down", "conn7", ProblemTask("pt_sftp_down", f"Down ({i})"))
            await uow.commit()
    rows = await _tasks(uow_factory)
    assert len(rows) == 1
    t = rows[0]
    assert (t["document_number"], t["document_type"], t["assigned_user_id"], t["title"]) == (
        "PT:sftp_down:conn7", "pt_module", OPERATOR, "Down (2)")
    assert t["notified_at"] is None and t["is_completed"] is False


async def test_resolve_completes_and_a_new_occurrence_opens_a_new_task(uow_factory):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        await SVC.raise_problem(uow.session, "e", "1", ProblemTask("pt_e", "t"))
        assert await SVC.resolve_problem(uow.session, "e", "1") == 1
        assert await SVC.resolve_problem(uow.session, "e", "1") == 0
        await SVC.raise_problem(uow.session, "e", "1", ProblemTask("pt_e", "again"))
        await uow.commit()
    rows = await _tasks(uow_factory)
    assert [(r["is_completed"], r["title"]) for r in rows] == [(True, "t"), (False, "again")]
    assert rows[0]["completed_at"] is not None


async def test_task_failure_never_fails_the_unit(uow_factory, person):
    broken = TaskService(table=TASKS, prefix="PT", document_type="pt_module", assigned_role="x" * 60,
                         assignee=_assignee)  # assigned_role too long for the column
    async with uow_factory(person) as uow:
        uow.session.add(Widget(code="W", name="kept"))
        await uow.session.flush()
        assert await broken.raise_problem(uow.session, "e", "1", ProblemTask("pt_e", "t")) is None
        await uow.commit()
    async with uow_factory(person) as uow:
        assert (await uow.session.execute(select(Widget.code))).scalars().all() == ["W"]
    assert await _tasks(uow_factory) == []


def test_problem_key_length_is_checked():
    import pytest
    with pytest.raises(ValueError):
        SVC.problem_key("x" * 30, "y" * 10)
