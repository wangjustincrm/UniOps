"""要求收件人动手的 VMS 邮件必须在 Task Inbox 里有落点。

覆盖两条原本只有邮件、没有任务的通知:
  - 访客超时未签出(notify_host_overdue,每天重发)→ Host 的 check_out_visitor
  - PPE 备货请求(notify_janitor_ppe_request)     → Janitor 的 prepare_ppe
以及两者在单据状态走过去之后的自动收口。

告知/抄送类(审批结果、4h 升级给部门经理)按约定只发信不建任务,这里不覆盖。
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.task_mirror import Task
from app.models.vms_config import VmsConfig
from app.models.visit import AccessArea, Visit, VisitPurpose, VisitStatus
from app.models.visitor import Visitor, VisitorType
from app.services import notifications as notifications_svc
from app.services import scheduled_jobs as jobs
from app.services import visit_tasks

from tests.conftest import make_user

UTC = timezone.utc


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _mk_visitor(db) -> Visitor:
    v = Visitor(first_name="Jo", last_name=f"V{uuid.uuid4().hex[:4]}",
                company_name="ACME Foods", visitor_type=VisitorType.supplier)
    db.add(v)
    await db.flush()
    return v


async def _mk_visit(db, *, host, visitor, status, planned_departure=None, **kw) -> Visit:
    v = Visit(
        visitor_id=visitor.id, host_id=host.id, created_by=host.id,
        visit_date=date(2026, 6, 15),
        planned_arrival=datetime(2026, 6, 15, 9, 0, tzinfo=UTC),
        planned_departure=planned_departure,
        visit_purpose=VisitPurpose.meeting, access_area=AccessArea.office,
        status=status, **kw,
    )
    db.add(v)
    await db.flush()
    return v


async def _open(db, task_type, visit_id):
    return (await db.execute(select(Task).where(
        Task.type == task_type, Task.document_type == visit_tasks.DOC_TYPE_VISIT,
        Task.document_id == visit_id, Task.is_completed.is_(False)))).scalars().all()


# ── 访客超时未签出 → Host 的签出任务 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_overdue_reminder_raises_check_out_task(test_engine, monkeypatch):
    """超时提醒信必须同时在 Host 的收件箱里留下一条签出任务。"""
    monkeypatch.setattr(notifications_svc, "_send_email",
                        lambda **kw: _true())
    async with _factory(test_engine)() as db:
        host = await make_user(test_engine, role="employee")
        visitor = await _mk_visitor(db)
        visit = await _mk_visit(db, host=host, visitor=visitor,
                                status=VisitStatus.checked_in,
                                planned_departure=datetime(2026, 6, 15, 17, 0, tzinfo=UTC),
                                actual_arrival=datetime(2026, 6, 15, 9, 5, tzinfo=UTC))
        await db.commit()

        await notifications_svc.notify_host_overdue(
            db, visit=visit, visitor=visitor, host=host)
        await db.flush()

        tasks = await _open(db, visit_tasks.TASK_CHECK_OUT, visit.id)
        assert len(tasks) == 1, "超时未签出邮件没有对应的 Task Inbox 条目"
        assert tasks[0].assigned_user_id == host.id


@pytest.mark.asyncio
async def test_check_out_task_not_duplicated_on_daily_resend(test_engine, monkeypatch):
    """该提醒每 24h 重发一次,任务不能跟着叠。"""
    monkeypatch.setattr(notifications_svc, "_send_email", lambda **kw: _true())
    async with _factory(test_engine)() as db:
        host = await make_user(test_engine, role="employee")
        visitor = await _mk_visitor(db)
        visit = await _mk_visit(db, host=host, visitor=visitor,
                                status=VisitStatus.checked_in,
                                planned_departure=datetime(2026, 6, 15, 17, 0, tzinfo=UTC))
        await db.commit()

        for _ in range(3):
            await notifications_svc.notify_host_overdue(
                db, visit=visit, visitor=visitor, host=host)
        await db.flush()

        assert len(await _open(db, visit_tasks.TASK_CHECK_OUT, visit.id)) == 1


@pytest.mark.asyncio
async def test_check_out_task_closes_after_checkout(test_engine, monkeypatch):
    monkeypatch.setattr(notifications_svc, "_send_email", lambda **kw: _true())
    async with _factory(test_engine)() as db:
        host = await make_user(test_engine, role="employee")
        visitor = await _mk_visitor(db)
        visit = await _mk_visit(db, host=host, visitor=visitor,
                                status=VisitStatus.checked_in,
                                planned_departure=datetime(2026, 6, 15, 17, 0, tzinfo=UTC))
        await db.commit()
        await notifications_svc.notify_host_overdue(
            db, visit=visit, visitor=visitor, host=host)
        await db.flush()
        assert len(await _open(db, visit_tasks.TASK_CHECK_OUT, visit.id)) == 1

        visit.status = VisitStatus.checked_out
        await db.flush()
        assert await visit_tasks.close_settled_visit_tasks(db) == 1
        assert await _open(db, visit_tasks.TASK_CHECK_OUT, visit.id) == []


# ── PPE 备货请求 → Janitor 的备货任务 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_ppe_request_raises_prepare_task_for_janitor(test_engine, monkeypatch):
    """PPE 备货信必须给配置的 Janitor 留一条任务。

    注意这条锚在 visit 上,和 compliance 的 vms_ppe(锚在 visitor、打badge时建)
    是两回事,不能互相顶替。
    """
    monkeypatch.setattr(notifications_svc, "_send_email", lambda **kw: _true())
    async with _factory(test_engine)() as db:
        host = await make_user(test_engine, role="employee")
        janitor = await make_user(test_engine, role="employee")
        visitor = await _mk_visitor(db)
        visit = await _mk_visit(db, host=host, visitor=visitor,
                                status=VisitStatus.confirmed, ppe_requested={"items": [{"clothing_size": "L", "footwear": "shoe_covers"}]})
        # conftest 已经种了一行空配置(notify_janitor_ppe_request 读的是
        # `SELECT ... FROM vms_config LIMIT 1`),所以要改它而不是再插一行。
        cfg = (await db.execute(select(VmsConfig).limit(1))).scalar_one()
        cfg.notification_contacts = {"ppe_email": janitor.email}
        await db.commit()

        await notifications_svc.notify_janitor_ppe_request(
            db, visit=visit, visitor=visitor, host=host)
        await db.flush()

        tasks = await _open(db, visit_tasks.TASK_PREPARE_PPE, visit.id)
        assert len(tasks) == 1, "PPE 备货邮件没有对应的 Task Inbox 条目"
        assert tasks[0].assigned_user_id == janitor.id


@pytest.mark.asyncio
async def test_prepare_ppe_task_closes_once_visitor_arrives(test_engine):
    """访客已到场(或取消/no-show)后备货窗口关闭,任务收口。"""
    async with _factory(test_engine)() as db:
        host = await make_user(test_engine, role="employee")
        janitor = await make_user(test_engine, role="employee")
        visitor = await _mk_visitor(db)
        visit = await _mk_visit(db, host=host, visitor=visitor,
                                status=VisitStatus.confirmed, ppe_requested={"items": [{"clothing_size": "L", "footwear": "shoe_covers"}]})
        await db.commit()
        await visit_tasks.open_ppe_prep_task(
            db, visit=visit, visitor=visitor, janitor_id=janitor.id)
        await db.flush()
        assert len(await _open(db, visit_tasks.TASK_PREPARE_PPE, visit.id)) == 1

        visit.status = VisitStatus.checked_in
        await db.flush()
        await visit_tasks.close_settled_visit_tasks(db)
        assert await _open(db, visit_tasks.TASK_PREPARE_PPE, visit.id) == []


@pytest.mark.asyncio
async def test_no_task_when_contact_is_not_a_user(test_engine):
    """联系人邮箱匹配不到在职用户时不建无主任务(会被按角色广播给全公司)。"""
    async with _factory(test_engine)() as db:
        host = await make_user(test_engine, role="employee")
        visitor = await _mk_visitor(db)
        visit = await _mk_visit(db, host=host, visitor=visitor,
                                status=VisitStatus.confirmed, ppe_requested={"items": [{"clothing_size": "L", "footwear": "shoe_covers"}]})
        await db.commit()
        assert await visit_tasks.open_ppe_prep_task(
            db, visit=visit, visitor=visitor, janitor_id=None) is None
        assert await _open(db, visit_tasks.TASK_PREPARE_PPE, visit.id) == []


async def _true(**_kw):
    return True
