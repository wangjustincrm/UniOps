"""Service/Project PO 完成日到期 → 催 requester 建 GR。

扫描器本身(scan_due_service_pos)是纯查询,可以拿固定的 `today` 驱动,不依赖
真实时钟。run_service_gr_due 那几个用例把 SMTP 层打桩,断言的是「谁被通知了」
而不是「邮件长什么样」。
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as session_module
from app.crud import user as user_crud
from app.crud.config import get_or_create as get_config
from app.models.department import Department
from app.models.gr import GoodsReceipt
from app.models.notification_log import NotificationLog
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.tasks import service_gr_due as mod
from app.tasks.service_gr_due import run_service_gr_due, scan_due_service_pos

pytestmark = pytest.mark.asyncio

TODAY = date(2026, 8, 21)


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed(
    db,
    *,
    po_type: int = 4,
    completion_date: date | None = None,
    po_status: str = "issued",
    with_pr: bool = True,
    department_id=None,
    owner=None,
):
    """建一条 vendor + requester + PR + PO 的最小链路。

    种子必须建在同一个 async session 里 —— conftest 的 seeded_vendor 走另一条
    未提交的 psycopg2 连接,async engine 看不见(FK 违约)。
    """
    tag = uuid.uuid4().hex[:8]
    vendor = Vendor(code=f"V-{tag}", name="Acme Services", category="supplier",
                    contact_name="AP", contact_email="ap@acme.example")
    db.add(vendor)
    requester = await user_crud.create(db, RegisterRequest(
        email=f"req-{tag}@example.com", password="TestPass1!",
        full_name="Ryan Requester", role="requester"))
    if department_id is not None:
        requester.department_id = department_id
    await db.flush()

    pr = None
    if with_pr:
        pr = PurchaseRequest(
            number=f"PR-20260801-{tag[:4]}", title="Annual duct cleaning",
            type=po_type, status="approved", vendor_id=vendor.id,
            vendor_name=vendor.name, currency="CAD", amount=Decimal("1000.00"),
            created_by=requester.id, service_completion_date=completion_date,
            owner_id=owner.id if owner is not None else None,
        )
        db.add(pr)
        await db.flush()

    po = PurchaseOrder(
        number=f"PO-20260801-{tag[:4]}", title="Annual duct cleaning",
        type=po_type, status=po_status, vendor_id=vendor.id, vendor_name=vendor.name,
        currency="CAD", subtotal=Decimal("1000.00"), tax_amount=Decimal("0.00"),
        total=Decimal("1000.00"), created_by=requester.id,
        pr_id=pr.id if pr else None,
    )
    db.add(po)
    await db.flush()
    return po, pr, requester, vendor


async def _ids(db, *, days: int = 5, **kw):
    """便捷:建一条「完成日在 `days` 天前」的链路,返回 scan 结果里的 PO id 集合。"""
    po, pr, requester, _ = await _seed(
        db, completion_date=TODAY - timedelta(days=days), **kw)
    return po, pr, requester


async def _person(db, *, role: str = "requester", department_id=None, name="Olive Owner"):
    tag = uuid.uuid4().hex[:8]
    u = await user_crud.create(db, RegisterRequest(
        email=f"own-{tag}@example.com", password="TestPass1!",
        full_name=name, role=role))
    if department_id is not None:
        u.department_id = department_id
    await db.flush()
    return u


# ── 扫描命中条件 ──────────────────────────────────────────────────────────────

async def test_service_po_past_its_completion_date_is_due(test_engine):
    async with _factory(test_engine)() as db:
        po, _, _ = await _ids(db, days=5)
        due = await scan_due_service_pos(db, reminder_days=1, today=TODAY)
        assert po.id in [d.po.id for d in due]
        assert next(d for d in due if d.po.id == po.id).days_over == 5
        await db.rollback()


async def test_project_po_type_6_is_also_due(test_engine):
    """Type 6(Project-Related)走的是同一条服务 GR 流程(api/v1/gr.py 的
    is_service),所以必须一起被扫到 —— 否则它会永远静默地留在集合外。"""
    async with _factory(test_engine)() as db:
        po, _, _ = await _ids(db, days=5, po_type=6)
        due = await scan_due_service_pos(db, reminder_days=1, today=TODAY)
        assert po.id in [d.po.id for d in due]
        await db.rollback()


@pytest.mark.parametrize("po_type", [1, 2, 3, 5])
async def test_physical_pos_are_never_due(test_engine, po_type):
    async with _factory(test_engine)() as db:
        po, _, _ = await _ids(db, days=90, po_type=po_type)
        due = await scan_due_service_pos(db, reminder_days=1, today=TODAY)
        assert po.id not in [d.po.id for d in due]
        await db.rollback()


async def test_the_threshold_day_itself_is_not_yet_due(test_engine):
    """完成日 + reminder_days == 今天 → 还不催。和 agreement_overdue 的宽限期
    边界口径一致:阈值当天仍在阈值内。"""
    async with _factory(test_engine)() as db:
        po, _, _ = await _ids(db, days=1)
        assert po.id not in [
            d.po.id for d in await scan_due_service_pos(db, reminder_days=1, today=TODAY)
        ]
        # 再过一天就该催了
        assert po.id in [
            d.po.id for d in await scan_due_service_pos(
                db, reminder_days=1, today=TODAY + timedelta(days=1))
        ]
        await db.rollback()


async def test_a_future_completion_date_is_not_due(test_engine):
    async with _factory(test_engine)() as db:
        po, _, _, _ = await _seed(db, completion_date=TODAY + timedelta(days=30))
        assert po.id not in [
            d.po.id for d in await scan_due_service_pos(db, reminder_days=1, today=TODAY)
        ]
        await db.rollback()


async def test_pr_without_a_completion_date_is_skipped(test_engine):
    """存量口径:上线前的 PR 这一列全空,扫描必须跳过而不是猜一个日期。"""
    async with _factory(test_engine)() as db:
        po, _, _, _ = await _seed(db, completion_date=None)
        assert po.id not in [
            d.po.id for d in await scan_due_service_pos(db, reminder_days=1, today=TODAY)
        ]
        await db.rollback()


async def test_po_without_a_pr_is_skipped(test_engine):
    """无 PR = 无 requester。放进去会落到 notification 的 requester 角色池群发,
    正是 2026-08-05 那次 59 人事故。"""
    async with _factory(test_engine)() as db:
        po, _, _, _ = await _seed(db, with_pr=False)
        assert po.id not in [
            d.po.id for d in await scan_due_service_pos(db, reminder_days=1, today=TODAY)
        ]
        await db.rollback()


@pytest.mark.parametrize("po_status", ["draft", "pending_approval", "fully_received", "closed"])
async def test_pos_outside_the_open_statuses_are_skipped(test_engine, po_status):
    async with _factory(test_engine)() as db:
        po, _, _ = await _ids(db, days=90, po_status=po_status)
        assert po.id not in [
            d.po.id for d in await scan_due_service_pos(db, reminder_days=1, today=TODAY)
        ]
        await db.rollback()


@pytest.mark.parametrize("po_status", ["approved", "issued", "partially_received"])
async def test_all_three_open_statuses_are_scanned(test_engine, po_status):
    """服务 GR 从 approved 起就能建,所以 approved 也必须在扫描集合里 —— 只看
    issued 会漏掉「已批准但还没下单」的那一段。"""
    async with _factory(test_engine)() as db:
        po, _, _ = await _ids(db, days=5, po_status=po_status)
        assert po.id in [
            d.po.id for d in await scan_due_service_pos(db, reminder_days=1, today=TODAY)
        ]
        await db.rollback()


# ── 已有 GR 就收口 ────────────────────────────────────────────────────────────

async def _add_gr(db, po, requester, *, status: str):
    gr = GoodsReceipt(
        number=f"GR-{uuid.uuid4().hex[:8]}", title=po.title, gr_type="service",
        po_id=po.id, po_number=po.number, vendor_id=po.vendor_id,
        vendor_name=po.vendor_name, procurement_type=po.type,
        status=status, created_by=requester.id,
    )
    db.add(gr)
    await db.flush()
    return gr


@pytest.mark.parametrize("gr_status", ["pending_ack", "collection_pending", "confirmed"])
async def test_a_live_gr_stops_the_nudge(test_engine, gr_status):
    async with _factory(test_engine)() as db:
        po, _, requester = await _ids(db, days=90)
        await _add_gr(db, po, requester, status=gr_status)
        assert po.id not in [
            d.po.id for d in await scan_due_service_pos(db, reminder_days=1, today=TODAY)
        ]
        await db.rollback()


@pytest.mark.parametrize("gr_status", ["rejected", "cancelled"])
async def test_a_void_gr_does_not_stop_the_nudge(test_engine, gr_status):
    """作废的 GR 没有履行收货义务,requester 还得建一张好的 —— 所以继续催。"""
    async with _factory(test_engine)() as db:
        po, _, requester = await _ids(db, days=90)
        await _add_gr(db, po, requester, status=gr_status)
        assert po.id in [
            d.po.id for d in await scan_due_service_pos(db, reminder_days=1, today=TODAY)
        ]
        await db.rollback()


# ── run_service_gr_due:开关、dry-run、建任务、幂等 ───────────────────────────

async def _configure(db, **notification_overrides):
    cfg = await get_config(db)
    cfg.notification_settings = {**(cfg.notification_settings or {}), **notification_overrides}
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(cfg, "notification_settings")
    await db.flush()
    return cfg


class _Spy:
    """记录谁被通知了,不真发邮件。"""

    def __init__(self):
        self.tasks: list = []
        self.admin_alerts: list = []
        self.escalations: list = []


@pytest.fixture
def spy(monkeypatch):
    s = _Spy()

    async def fake_dispatch(task, db, **kwargs):
        template_key = kwargs.get("template_key")
        s.tasks.append((task.id, template_key))
        # 真的 dispatch 会写一条 notification_logs,而升级的前置条件
        # (_has_been_sent)正是读它 —— 替身不写就等于把那条链路掐断,
        # 升级永远不会发生,测试会「因为替身而绿/红」。
        db.add(NotificationLog(
            task_id=task.id, recipient_email="spy@example.com",
            channel="email", template_key=template_key, status="ok", attempt=1,
        ))
        await db.flush()

    async def fake_admin_alert(subject, body, db=None):
        s.admin_alerts.append((subject, body))

    async def fake_escalate(db, cfg, due, task):
        s.escalations.append(due.po.number)
        return True

    monkeypatch.setattr(mod, "dispatch_task_notification", fake_dispatch)
    monkeypatch.setattr(mod, "send_admin_alert", fake_admin_alert)
    monkeypatch.setattr(mod, "_escalate_to_manager", fake_escalate)
    return s


@pytest.fixture
def patched_sessions(test_engine, monkeypatch):
    """run_service_gr_due 自己开 session,把工厂指到测试库。"""
    factory = _factory(test_engine)
    monkeypatch.setattr(session_module, "AsyncSessionLocal", factory)
    return factory


async def test_the_toggle_off_skips_the_whole_run(test_engine, spy, patched_sessions):
    async with patched_sessions() as db:
        po, _, _ = await _ids(db, days=90)
        await _configure(db, service_gr_due_enabled=False)
        await db.commit()

    await run_service_gr_due()

    assert spy.tasks == [] and spy.admin_alerts == []
    async with patched_sessions() as db:
        assert await mod._open_confirm_receipt_task(db, po.id) is None


async def test_dry_run_reports_to_admin_and_creates_no_task(test_engine, spy, patched_sessions):
    async with patched_sessions() as db:
        po, _, _ = await _ids(db, days=90)
        await _configure(db, service_gr_due_enabled=True, service_gr_due_dry_run=True)
        await db.commit()

    await run_service_gr_due()

    assert spy.tasks == [], "dry-run 不该通知 requester"
    assert len(spy.admin_alerts) == 1
    assert po.number in spy.admin_alerts[0][1]
    async with patched_sessions() as db:
        assert await mod._open_confirm_receipt_task(db, po.id) is None, "dry-run 不该建任务"


async def test_a_live_run_creates_a_confirm_receipt_task_for_the_requester(
    test_engine, spy, patched_sessions,
):
    async with patched_sessions() as db:
        po, _, requester = await _ids(db, days=5)
        await _configure(db, service_gr_due_enabled=True, service_gr_due_dry_run=False)
        await db.commit()

    await run_service_gr_due()

    async with patched_sessions() as db:
        task = await mod._open_confirm_receipt_task(db, po.id)
        assert task is not None
        assert task.assigned_user_id == requester.id, "必须点名到 PR 的 requester"
        assert task.assigned_role == "requester"
        assert task.document_type == "po" and task.document_id == po.id
    assert (task.id, "service_gr_due") in spy.tasks


async def test_a_second_sweep_reuses_the_task_instead_of_duplicating_it(
    test_engine, spy, patched_sessions,
):
    async with patched_sessions() as db:
        po, _, _ = await _ids(db, days=5)
        await _configure(db, service_gr_due_enabled=True, service_gr_due_dry_run=False)
        await db.commit()

    await run_service_gr_due()
    await run_service_gr_due()

    async with patched_sessions() as db:
        rows = (await db.execute(select(Task).where(
            Task.type == "confirm_receipt",
            Task.document_id == po.id,
        ))).scalars().all()
    assert len(rows) == 1, "重复扫描不能堆任务,只能重发提醒"
    assert len([t for t in spy.tasks if t[0] == rows[0].id]) == 2, "但两轮都要重发提醒"


async def test_the_first_sweep_never_escalates_however_overdue(
    test_engine, spy, patched_sessions,
):
    """回填进来的存量单据完成日可能已经过去几个月,但 requester 一次都没被催过。
    「催一封、同时告状给他经理」是这个功能上线第一天就会被当成坏了的行为。"""
    async with patched_sessions() as db:
        stale, _, _ = await _ids(db, days=300)
        await _configure(db, service_gr_due_enabled=True, service_gr_due_dry_run=False)
        await db.commit()

    await run_service_gr_due()

    # 断言收敛到本用例这张单:run_service_gr_due 扫全库,同一会话里别的用例
    # commit 的 PO 也会被一起扫到(它们可能已经催过、因而合法地升级)。
    assert stale.number not in spy.escalations, "第一轮只该催 requester"
    async with patched_sessions() as db:
        task = await mod._open_confirm_receipt_task(db, stale.id)
    assert (task.id, "service_gr_due") in spy.tasks, "但催办信要发出去"


async def test_escalation_only_fires_past_the_manager_threshold(
    test_engine, spy, patched_sessions,
):
    """天数到了**且**已经催过,才升级。两条都建在同一轮,所以第二轮跑时
    reminded_before 都为真,差别只剩天数。"""
    async with patched_sessions() as db:
        fresh, _, _ = await _ids(db, days=1)      # 刚过 reminder,还没到升级
        stale, _, _ = await _ids(db, days=30)     # 远超升级阈值
        await _configure(db, service_gr_due_enabled=True, service_gr_due_dry_run=False)
        await db.commit()

    await run_service_gr_due()      # 第一轮:只催
    after_first = list(spy.escalations)
    await run_service_gr_due()      # 第二轮:天数够的那条才升级

    assert stale.number not in after_first, "第一轮不该升级"
    assert stale.number in spy.escalations
    assert fresh.number not in spy.escalations


# ── 升级信的幂等(不打桩 _escalate_to_manager,直接验 notification_logs)────

async def test_escalation_is_sent_only_once_per_task(test_engine, monkeypatch, patched_sessions):
    sent: list[str] = []

    async def fake_send_email(to, subject, html, **kw):
        sent.append(to)

    monkeypatch.setattr("app.services.email.send_email", fake_send_email)

    async with patched_sessions() as db:
        dept = Department(code=f"D{uuid.uuid4().hex[:6]}", name="Facilities")
        db.add(dept)
        await db.flush()
        manager = await user_crud.create(db, RegisterRequest(
            email=f"mgr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Mary Manager", role="dept_manager"))
        manager.department_id = dept.id
        po, _, _ = await _ids(db, days=30, department_id=dept.id)
        await _configure(db, service_gr_due_enabled=True, service_gr_due_dry_run=False)
        await db.commit()

    await run_service_gr_due()      # 催
    await run_service_gr_due()      # 升级(此时已催过)
    await run_service_gr_due()      # 不该再升级

    assert sent.count(manager.email) == 1, "升级信只发一次"
    async with patched_sessions() as db:
        task = await mod._open_confirm_receipt_task(db, po.id)
        logs = (await db.execute(select(NotificationLog).where(
            NotificationLog.task_id == task.id,
            NotificationLog.template_key == "service_gr_escalation",
        ))).scalars().all()
    assert len(logs) == 1


# ── Owner 路由 ────────────────────────────────────────────────────────────────
#
# 服务单常由行政代提:requester 是提单的人,Owner 才是知道活儿干完没干完的人。
# 催办必须落在 Owner 身上,否则收件人根本答不上来。

async def test_the_nudge_goes_to_the_owner_not_the_requester(
    test_engine, spy, patched_sessions,
):
    async with patched_sessions() as db:
        owner = await _person(db)
        po, _, requester = await _ids(db, days=5, owner=owner)
        await _configure(db, service_gr_due_enabled=True, service_gr_due_dry_run=False)
        await db.commit()

    await run_service_gr_due()

    async with patched_sessions() as db:
        task = await mod._open_confirm_receipt_task(db, po.id)
    assert task is not None
    assert task.assigned_user_id == owner.id, "点名 Owner"
    # 否定断言配肯定断言:光断言「不是 requester」会被「谁都不是」满足。
    assert task.assigned_user_id != requester.id
    assert task.assigned_role == "requester", "池标签不变,变的是受理人"
    assert (task.id, "service_gr_due") in spy.tasks


async def test_without_an_owner_the_nudge_still_goes_to_the_requester(
    test_engine, spy, patched_sessions,
):
    """owner_id 为空 = 「就是 requester」。上线前的存量单据全是这一种,
    回落错了等于把所有老服务单的催办变成 NULL 受理人 → 角色群发。"""
    async with patched_sessions() as db:
        po, pr, requester = await _ids(db, days=5)
        assert pr.owner_id is None
        await _configure(db, service_gr_due_enabled=True, service_gr_due_dry_run=False)
        await db.commit()

    await run_service_gr_due()

    async with patched_sessions() as db:
        task = await mod._open_confirm_receipt_task(db, po.id)
    assert task is not None
    assert task.assigned_user_id == requester.id


async def test_escalation_goes_to_the_owners_manager_not_the_requesters(
    test_engine, monkeypatch, patched_sessions,
):
    """升级的语义是「我催了他、他没动」—— 要找**他**的经理。

    Owner 和 requester 常常不在一个部门,按 requester 找会把状告到一个管不着
    这件事的经理那里,而真正压得动的人一无所知。这里给两个部门各配一个经理,
    断言只有 Owner 那位收到信(另一位必须一封都没有 —— 否则「都收到」也能
    让「Owner 的经理收到了」这条断言通过)。
    """
    sent: list[str] = []

    async def fake_send_email(to, subject, html, **kw):
        sent.append(to)

    monkeypatch.setattr("app.services.email.send_email", fake_send_email)

    async with patched_sessions() as db:
        owner_dept = Department(code=f"D{uuid.uuid4().hex[:6]}", name="Engineering")
        req_dept = Department(code=f"D{uuid.uuid4().hex[:6]}", name="Admin")
        db.add_all([owner_dept, req_dept])
        await db.flush()
        owner_mgr = await _person(
            db, role="dept_manager", department_id=owner_dept.id, name="Olga OwnerMgr")
        req_mgr = await _person(
            db, role="dept_manager", department_id=req_dept.id, name="Rita ReqMgr")
        owner = await _person(db, department_id=owner_dept.id)
        po, _, _ = await _ids(
            db, days=30, owner=owner, department_id=req_dept.id)
        await _configure(db, service_gr_due_enabled=True, service_gr_due_dry_run=False)
        await db.commit()

    await run_service_gr_due()      # 催 Owner
    await run_service_gr_due()      # 已催过 + 天数够 → 升级

    assert owner_mgr.email in sent, "Owner 的部门经理要收到"
    assert req_mgr.email not in sent, "requester 的部门经理不该收到"
    async with patched_sessions() as db:
        task = await mod._open_confirm_receipt_task(db, po.id)
        logs = (await db.execute(select(NotificationLog).where(
            NotificationLog.task_id == task.id,
            NotificationLog.template_key == "service_gr_escalation",
        ))).scalars().all()
    assert len(logs) == 1
