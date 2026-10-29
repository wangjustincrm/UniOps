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


# ── review 2026-10-28 ────────────────────────────────────────────────────────


def test_document_type_must_fit_the_shared_column():
    import pytest

    from app.platform.tasks import DOCTYPE_MAX
    assert DOCTYPE_MAX == 10 and TASKS.c.document_type.type.length == 10  # epms-api tasks.document_type
    with pytest.raises(ValueError):
        TaskService(table=TASKS, prefix="OB", document_type="order_bridge", assigned_role="r", assignee=_assignee)
    TaskService(table=TASKS, prefix="OB", document_type="obr", assigned_role="r", assignee=_assignee)


async def test_a_title_too_long_for_the_column_is_raised_not_swallowed(uow_factory):
    import pytest
    async with uow_factory(SYSTEM_ACTOR) as uow:
        with pytest.raises(ValueError, match="255"):
            await SVC.raise_problem(uow.session, "e", "1", ProblemTask("pt_e", "x" * 256))
        assert await SVC.raise_problem(uow.session, "e", "1", ProblemTask("pt_e", "x" * 255)) is not None


async def test_tasks_are_written_as_notified_while_external_email_is_off(uow_factory):
    flag = {"on": False}
    svc = TaskService(table=TASKS, prefix="PT", document_type="pt_module", assigned_role="r",
                      assignee=_assignee, notify=lambda: flag["on"])
    async with uow_factory(SYSTEM_ACTOR) as uow:
        await svc.raise_problem(uow.session, "quiet", "1", ProblemTask("pt_e", "dev"))
        flag["on"] = True
        await svc.raise_problem(uow.session, "loud", "1", ProblemTask("pt_e", "prod"))
        await uow.commit()
    rows = {r["title"]: r for r in await _tasks(uow_factory)}
    assert rows["dev"]["notified_at"] is not None  # the epms notifier skips it
    assert rows["prod"]["notified_at"] is None


async def test_concurrent_units_raising_the_same_problem_make_one_task(uow_factory):
    import asyncio

    first = uow_factory(SYSTEM_ACTOR)
    await first.__aenter__()
    await SVC.raise_problem(first.session, "race", "9", ProblemTask("pt_e", "first"))

    async def second():
        async with uow_factory(SYSTEM_ACTOR) as uow:
            await SVC.raise_problem(uow.session, "race", "9", ProblemTask("pt_e", "second"))
            await uow.commit()

    t = asyncio.ensure_future(second())
    await asyncio.sleep(0.3)
    assert not t.done(), "the second unit waits for the first one's lock"
    await first.commit()
    await first.__aexit__(None, None, None)
    await asyncio.wait_for(t, timeout=5)
    rows = await _tasks(uow_factory)
    assert [(r["title"], r["is_completed"]) for r in rows] == [("second", False)]
