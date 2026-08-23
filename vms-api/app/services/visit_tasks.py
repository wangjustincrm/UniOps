"""Task Inbox 落点 —— 给「要求收件人去做事」的 VMS 邮件配任务行。

VMS 一共发六类邮件。其中培训 / PPE 合规两类已经由 `services/compliance.py`
配了 Task;审批结果通知、4 小时升级信是**告知 / 抄送**性质,责任人另有其人,
按约定只发信不建任务。剩下两类是明确要求收件人动手的,却只有邮件:

  - 访客超时未签出(`notify_host_overdue`,每天重发)—— 信里写着「请在 VMS 里
    把访客签出」,收件人是 Host,但收件箱里没有任何东西。
  - PPE 备货请求(`notify_janitor_ppe_request`)—— 要求 Janitor 提前备好装备。
    注意这跟 compliance 的 `vms_ppe` 任务**不是一回事**:那条是访客个人的
    PPE 培训记录,在打badge时才建,锚在 visitor 上;这条锚在 visit 上。

两类都锚 `document_type="vms_visit"`(epms `notification._task_link` 已把
vms_visit 深链到 VMS 根路径,由 VMS 自己按角色路由),靠 `type` 区分。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_mirror import Task
from app.models.visit import Visit, VisitStatus
from app.models.visitor import Visitor

log = logging.getLogger(__name__)

# `tasks.document_type` 物理上是 varchar(10)(epms-api 拥有该 schema),
# "vms_visit" 9 字符,贴着上限,别改长。
DOC_TYPE_VISIT = "vms_visit"

TASK_CHECK_OUT = "check_out_visitor"
TASK_PREPARE_PPE = "prepare_ppe"

# assigned_role 与 compliance.py::_TASK_ROLE 同一套命名(vms_ppe_contact 就是
# 那位配置出来的 Janitor)。tasks.assigned_role 是裸字符串、无外键,而且这两类
# 任务永远带具体 assigned_user_id,不会走 get_for_role 的角色广播分支。


def _visitor_label(visitor: Visitor) -> str:
    return f"{visitor.first_name} {visitor.last_name} ({visitor.company_name})"


async def _existing_open(db: AsyncSession, *, task_type: str, visit_id: uuid.UUID) -> Task | None:
    return (await db.execute(
        select(Task).where(
            Task.type == task_type,
            Task.document_type == DOC_TYPE_VISIT,
            Task.document_id == visit_id,
            Task.is_completed.is_(False),
        ).limit(1)
    )).scalar_one_or_none()


async def _ensure(
    db: AsyncSession, *, task_type: str, visit: Visit, assigned_user_id: uuid.UUID | None,
    assigned_role: str, title: str, description: str,
) -> Task | None:
    """幂等地建一条任务。已有开放任务就原样返回。

    `assigned_user_id` 为 None 时**不建任务**并返回 None —— 这与
    compliance.open_compliance_task 的取舍一致:一条没有受理人的
    NULL-assignee 任务会按 assigned_role 广播给全公司该角色的人
    (见 epms crud/task.py::get_for_role),对「给这位 Host / 这位 Janitor
    的活」来说是错的。邮件路径不受影响,照发。
    """
    if assigned_user_id is None:
        return None
    existing = await _existing_open(db, task_type=task_type, visit_id=visit.id)
    if existing is not None:
        return existing
    task = Task(
        type=task_type,
        priority="normal",
        document_type=DOC_TYPE_VISIT,
        document_id=visit.id,
        document_number=str(visit.id)[:8],
        assigned_role=assigned_role,
        assigned_user_id=assigned_user_id,
        title=title,
        description=description,
    )
    db.add(task)
    await db.flush()
    return task


async def open_check_out_task(
    db: AsyncSession, *, visit: Visit, visitor: Visitor, host_id: uuid.UUID | None,
) -> Task | None:
    """访客超时未签出 —— 给 Host 派「把访客签出」。

    超时提醒信每天重发,所以这里必须幂等:每个 visit 同时只有一条开放任务。
    """
    return await _ensure(
        db, task_type=TASK_CHECK_OUT, visit=visit,
        assigned_user_id=host_id, assigned_role="vms_host",
        title=f"Check out visitor — {_visitor_label(visitor)}",
        description=(
            f"{_visitor_label(visitor)} is still checked in past their planned "
            f"departure. Confirm they have left and check them out in VMS."
        ),
    )


async def open_ppe_prep_task(
    db: AsyncSession, *, visit: Visit, visitor: Visitor, janitor_id: uuid.UUID | None,
) -> Task | None:
    """PPE 备货 —— 给配置的 Janitor 派「按清单备好装备」。"""
    return await _ensure(
        db, task_type=TASK_PREPARE_PPE, visit=visit,
        assigned_user_id=janitor_id, assigned_role="vms_ppe_contact",
        title=f"Prepare PPE — {_visitor_label(visitor)}",
        description=(
            f"PPE was requested for {_visitor_label(visitor)}'s visit on "
            f"{visit.visit_date.isoformat()}. Please pre-stage the gear before "
            f"their planned arrival."
        ),
    )


async def close_settled_visit_tasks(db: AsyncSession) -> int:
    """把单据状态已经走过去的任务收口。由调度器每趟调用。

    - `check_out_visitor`:visit 不再是 checked_in(签出 / 取消)→ 事情做完了。
    - `prepare_ppe`:visit 已经不在「等着到访」的两个状态里(即已到场、已取消、
      已 no_show)→ 备货窗口已过。

    两者都不看 completed_by:这里没有「用户驳回」语义,状态完全由 visit 决定。
    """
    checked_in_visits = select(Visit.id).where(Visit.status == VisitStatus.checked_in)
    upcoming_visits = select(Visit.id).where(
        Visit.status.in_((VisitStatus.pending_approval, VisitStatus.confirmed))
    )
    rows = (await db.execute(select(Task).where(
        Task.document_type == DOC_TYPE_VISIT,
        Task.is_completed.is_(False),
        or_(
            (Task.type == TASK_CHECK_OUT) & Task.document_id.not_in(checked_in_visits),
            (Task.type == TASK_PREPARE_PPE) & Task.document_id.not_in(upcoming_visits),
        ),
    ))).scalars().all()
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    for task in rows:
        task.is_completed = True
        task.completed_at = now
    await db.flush()
    return len(rows)
