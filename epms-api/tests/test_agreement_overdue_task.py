"""缺票逾期通知必须在 Task Inbox 里有落点,并在票到齐后自动收口。

原实现只给协议 owner 发一封邮件(app/tasks/agreement_overdue.py),Task Inbox 里
没有任何对应条目 —— 收件人读完邮件之后,这件「还欠着票」的事就再无提示。
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

import app.db.session as sm
from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.tasks.agreement_overdue import (
    CHASE_TASK_TYPE,
    _close_settled_chase_tasks,
    _notify_owners,
    sweep_overdue_periods,
)


async def _agreement_with_overdue_period(db, *, owner, days_over: int = 30):
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v)
    await db.flush()
    agr = PurchaseAgreement(
        number=f"AGR-t{uuid.uuid4().hex[:6]}", title="Cleaning",
        agreement_type="recurring", vendor_id=v.id,
        vendor_name="Acme", status="active", owner_id=owner.id,
        valid_from=date.today() - timedelta(days=365),
        valid_to=date.today() + timedelta(days=365),
        created_by=owner.id,
    )
    db.add(agr)
    await db.flush()
    row = AgreementPaymentSchedule(
        agreement_id=agr.id, schedule_type="period", sequence=1, period_label="2026-06",
        expected_date=date.today() - timedelta(days=days_over),
        expected_amount=Decimal("500"), status="pending", overdue_after_days=7,
    )
    db.add(row)
    await db.flush()
    return agr, row


async def _owner(db):
    return await user_crud.create(db, RegisterRequest(
        email=f"o-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Olivia Owner", role="dept_manager"))


async def _open_chase(db, agr_id):
    return (await db.execute(select(Task).where(
        Task.type == CHASE_TASK_TYPE, Task.document_type == "agr",
        Task.document_id == agr_id, Task.is_completed.is_(False)))).scalars().all()


class _Cfg:
    """_notify_owners 只用到 SMTP 字段;发信本身在测试里必然失败并被吞掉,
    正好证明任务的创建不依赖 SMTP 成功。"""
    name = "Test Co"
    smtp_host = None
    smtp_port = None
    smtp_user = None
    smtp_password = None
    smtp_use_tls = None
    smtp_from = None


@pytest.mark.asyncio
async def test_overdue_sweep_raises_chase_task_for_owner():
    async with sm.AsyncSessionLocal() as db:
        owner = await _owner(db)
        agr, _row = await _agreement_with_overdue_period(db, owner=owner)

        flipped = await sweep_overdue_periods(db)
        assert len(flipped) == 1
        await _notify_owners(db, _Cfg(), flipped)
        await db.flush()

        tasks = await _open_chase(db, agr.id)
        assert len(tasks) == 1, "缺票逾期邮件没有对应的 Task Inbox 条目"
        assert tasks[0].assigned_user_id == owner.id
        assert tasks[0].document_number == agr.number


@pytest.mark.asyncio
async def test_chase_task_is_not_duplicated_across_sweeps():
    """扫描每天都跑,任务不能每天叠一条。"""
    async with sm.AsyncSessionLocal() as db:
        owner = await _owner(db)
        agr, row = await _agreement_with_overdue_period(db, owner=owner)

        flipped = await sweep_overdue_periods(db)
        await _notify_owners(db, _Cfg(), flipped)
        await db.flush()
        # 第二趟:该行已是 overdue,不会再 flip;直接重放通知这一步
        await _notify_owners(db, _Cfg(), [row])
        await db.flush()

        assert len(await _open_chase(db, agr.id)) == 1


@pytest.mark.asyncio
async def test_chase_task_closes_once_invoices_arrive():
    """期次被认领(不再 overdue)后,催票任务自动收口。"""
    async with sm.AsyncSessionLocal() as db:
        owner = await _owner(db)
        agr, row = await _agreement_with_overdue_period(db, owner=owner)
        flipped = await sweep_overdue_periods(db)
        await _notify_owners(db, _Cfg(), flipped)
        await db.flush()
        assert len(await _open_chase(db, agr.id)) == 1

        row.status = "received"          # 票到了
        await db.flush()
        await _close_settled_chase_tasks(db)
        await db.flush()

        assert await _open_chase(db, agr.id) == []


def test_agreement_tasks_deep_link_into_epms():
    """agr 任务的邮件深链必须指向 EPMS 的 /agreements/{id}。

    `_task_link` 的路由表原本没有 "agr",于是 confirm_period /
    chase_agreement_invoice 的邮件全都落到了默认分支(OA 的 /expenses/{id}),
    点开是一个不存在的报销单。
    """
    from app.core.config import settings
    from app.services.notification import _task_link

    agr_id = uuid.uuid4()
    link = _task_link("agr", agr_id)
    assert link == f"{settings.EPMS_URL}/agreements/{agr_id}"
    assert "/expenses/" not in link
