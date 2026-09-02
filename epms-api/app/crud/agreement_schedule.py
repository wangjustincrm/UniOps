"""排期行的落库与查询。周期日期算法在 app/services/agreement_schedule.py。"""
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.task import Task
from app.models.user import User
from app.schemas.agreement import MilestoneRowIn
from app.services.agreement_schedule import build_period_rows

logger = logging.getLogger(__name__)


def _resolve_milestone_amounts(
    row: MilestoneRowIn, ceiling: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """金额与百分比二选一录入,另一个推算。基数是 not_to_exceed —— 协议上唯一的
    总额字段。两个都给了就都存,不去纠正用户。"""
    amount, pct = row.expected_amount, row.amount_pct
    if amount is None and pct is not None and ceiling:
        amount = (ceiling * pct / Decimal("100")).quantize(Decimal("0.01"))
    elif pct is None and amount is not None and ceiling:
        pct = (amount / ceiling * Decimal("100")).quantize(Decimal("0.01"))
    return amount, pct


async def replace_milestone_rows(
    db: AsyncSession, agr: PurchaseAgreement, rows: list[MilestoneRowIn]
) -> None:
    """整体替换阶段行。只在 draft/returned 调用 —— 已认领的阶段不能被抹掉。"""
    existing = (await db.execute(
        select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id,
            AgreementPaymentSchedule.schedule_type == "milestone",
        )
    )).scalars().all()
    claimed = [r for r in existing if r.invoice_id is not None]
    if claimed:
        raise ValueError(
            f"{len(claimed)} milestone stage(s) already have an invoice matched to them "
            "and cannot be re-entered; detach the invoice first")
    for row in existing:
        await db.delete(row)
    await db.flush()

    for i, row in enumerate(rows, start=1):
        amount, pct = _resolve_milestone_amounts(row, agr.not_to_exceed)
        db.add(AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="milestone", sequence=i,
            milestone_name=row.milestone_name, expected_timing=row.expected_timing,
            expected_amount=amount, amount_pct=pct, status="pending",
        ))
    await db.flush()


async def list_rows(
    db: AsyncSession, agreement_id: uuid.UUID
) -> list[AgreementPaymentSchedule]:
    return list((await db.execute(
        select(AgreementPaymentSchedule)
        .where(AgreementPaymentSchedule.agreement_id == agreement_id)
        .order_by(AgreementPaymentSchedule.schedule_type,
                  AgreementPaymentSchedule.sequence)
    )).scalars().all())


async def ensure_period_rows(db: AsyncSession, agr: PurchaseAgreement) -> int:
    """协议转 active 时生成整个有效期的排期行。

    幂等:已有 period 行就什么都不做 —— 审批可能因 resync 之类的操作重入。
    非 recurring 协议直接返回 0。
    """
    if agr.agreement_type != "recurring" or not agr.recurring_type:
        return 0
    existing = (await db.execute(
        select(AgreementPaymentSchedule.id).where(
            AgreementPaymentSchedule.agreement_id == agr.id,
            AgreementPaymentSchedule.schedule_type == "period",
        ).limit(1)
    )).first()
    if existing:
        return 0

    rows = build_period_rows(
        recurring_type=agr.recurring_type, valid_from=agr.valid_from,
        valid_to=agr.valid_to, expected_invoice_day=agr.expected_invoice_day,
        anchor_month=agr.anchor_month,
        schedule_start_date=agr.schedule_start_date,
        active_months=agr.active_months,
    )
    for r in rows:
        db.add(AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="period", sequence=r.sequence,
            period_label=r.period_label, expected_date=r.expected_date,
            expected_amount=agr.expected_amount_per_period,
            tolerance_pct=agr.tolerance_pct, overdue_after_days=agr.overdue_after_days,
            status="pending",
        ))
    await db.flush()
    return len(rows)


async def claim_next_period(
    db: AsyncSession, agr: PurchaseAgreement, invoice
) -> AgreementPaymentSchedule | None:
    """按发票日期认领 —— 取 expected_date 离 invoice_date 最近的那个未认领期次。

    **这条规则取代了原来的 FIFO(按 sequence 取第一个)。** 原注释担心的是
    "8 月的网络费 9/3 才开票,按发票日期会错配一整期" —— 但那个担心的前提是
    拿 period_label 的月份去套发票日期。这里比的是 `expected_date`(该期
    *预计开票日*,由 expected_invoice_day 生成),9/3 的发票离 9/4 那行只差
    一天、离 8/4 那行差三十天,选出来的正是该被它填掉的那一行。两种情形都对。

    FIFO 真正的破绽是上线时点:协议往往已经跑了一两年才进系统,排期从合同
    首期就生成出来,于是 FIFO 永远从一个再也不会有发票的历史期次开始 ——
    每一张票都撞容差、每一张票都掉进 match_review,那道闸门就此变成噪音。
    用户实测反馈:2026-08 的发票被认到 2025-01 上。

    乱序到达(供应商补开上上个月的票)仍然落到最近的那一期;确实需要人工指定
    的,claim_specific_period 那条逃生舱照旧。

    `expected_date` 为空的行排到最后 —— 它们没有可比的日期,只能当兜底。
    距离相同(发票正好落在两期正中)时取 sequence 小的那个:先欠的先还。
    """
    rows = list((await db.execute(
        select(AgreementPaymentSchedule)
        .where(AgreementPaymentSchedule.agreement_id == agr.id,
               AgreementPaymentSchedule.schedule_type == "period",
               AgreementPaymentSchedule.status.in_(("pending", "overdue")))
        .order_by(AgreementPaymentSchedule.sequence)
    )).scalars().all())
    if not rows:
        return None
    inv_date = invoice.invoice_date
    row = min(
        rows,
        key=lambda r: (
            r.expected_date is None,
            abs((r.expected_date - inv_date).days) if r.expected_date is not None else 0,
            r.sequence,
        ),
    )

    # ── 金额校验(用户裁定 2026-08-13)────────────────────────────────────
    # 比的是发票**税前额**,不是含税总额。expected_amount_per_period 录的是
    # 合同价 —— 合同谈的是净价,税是法定加上去的,税率会变、一票还可能多税率。
    # 拿含税总额去比一个净额,任何带税协议**永远命中不了**:1,980 的月费开出
    # 2,237.40 的票,差的正好是那 13% 的 HST,于是每一张票都超容差、每一张票
    # 都掉进 match_review。这就是用户报上来的那张 INV-2026-0145。
    #
    # tolerance_pct 为 **NULL = 不做金额校验**;显式填 0 才是"必须分毫不差"。
    # 原来 `or Decimal("0")` 把两者混为一谈,于是"容差没填"被解释成了系统里
    # 最严的那档 —— 与字段留空的直觉正好相反。
    if row.expected_amount is not None and row.tolerance_pct is not None:
        span = row.expected_amount * row.tolerance_pct / Decimal("100")
        amount = Decimal(str(invoice.amount))
        if not (row.expected_amount - span <= amount <= row.expected_amount + span):
            return None

    row.status = "received"
    row.invoice_id = invoice.id
    await db.flush()
    return row


async def claim_specific_period(
    db: AsyncSession, agr: PurchaseAgreement, invoice, row_id: uuid.UUID
) -> AgreementPaymentSchedule:
    """人工指定期次(spec §4.3 step 5 的手动指派逃生舱,whole-branch review 补齐)。

    claim_next_period 认不到期次(超容差 / 排期已耗尽)就停在 match_review,
    schedule_id 留空 —— 协议 active 后容差字段不可编辑(EDITABLE_STATUSES),
    拒了重投也是同一行同一容差,永远认不到,PA 那道"未链接期次"闸门就再也
    过不去。这里让人工显式指定是哪一行,并且跳过金额容差校验 —— 人在主动
    覆盖它,校验的意义已经不在了,跟 claim_milestone 完全不做金额校验是
    同一个道理(设计 §5.2:预期与实际并排显示给人眼判断,不是让机器拦)。
    仍然要挡住跨协议 / 非 period 类型 / 已被认领的行,拒绝方式照抄
    claim_milestone。
    """
    row = (await db.execute(
        select(AgreementPaymentSchedule).where(AgreementPaymentSchedule.id == row_id)
    )).scalar_one_or_none()
    if row is None or row.agreement_id != agr.id or row.schedule_type != "period":
        raise ValueError("That billing period does not belong to this agreement")
    if row.invoice_id is not None:
        raise ValueError(
            f"{row.period_label} already has an invoice matched to it")
    row.status = "received"
    row.invoice_id = invoice.id
    await db.flush()
    return row


async def claim_milestone(
    db: AsyncSession, agr: PurchaseAgreement, invoice, row_id: uuid.UUID
) -> AgreementPaymentSchedule:
    row = (await db.execute(
        select(AgreementPaymentSchedule).where(AgreementPaymentSchedule.id == row_id)
    )).scalar_one_or_none()
    if row is None or row.agreement_id != agr.id or row.schedule_type != "milestone":
        raise ValueError("That milestone stage does not belong to this agreement")
    if row.invoice_id is not None:
        raise ValueError(
            f"Milestone '{row.milestone_name}' already has an invoice matched to it")
    # 不做金额校验(设计 §5.2):预期与实际并排显示给人眼判断,超支由协议 NTE 预警覆盖。
    row.status = "received"
    row.invoice_id = invoice.id
    await db.flush()
    return row


async def _confirm_assignee(
    db: AsyncSession, agr: PurchaseAgreement
) -> tuple[uuid.UUID | None, str | None]:
    """协议责任人优先;没设 owner 就落到该部门的在职经理。

    经理身份看 EFFECTIVE role —— base role(users.role)或者 user_roles 里的
    附加角色授权,两者都算,不能只查 users.role。access_scope._effective_role_codes
    / role_holder_ids 就是按这个口径判定"谁持有某个角色"的;只查 users.role 会漏掉
    只靠 grant 拿到 dept_manager 的人(code review finding,Task 7 修复轮)。

    Returns (assignee user id or None, the role that assignment came through —
    None if nothing resolved at all). The caller uses the second element as
    Task.assigned_role instead of hardcoding one: when owner_id resolves, the
    task is assigned to a SPECIFIC person and assigned_role should describe
    who they actually are, not always claim "dept_manager" (code review
    finding — harmless for visibility since assigned_user_id is set, but
    wrong stored data).
    """
    if agr.owner_id:
        role = (await db.execute(
            select(User.role).where(User.id == agr.owner_id)
        )).scalar_one_or_none()
        return agr.owner_id, (role or "dept_manager")
    if not agr.department_id:
        return None, None
    mgr_id = (await db.execute(text(
        "SELECT id::text FROM users "
        " WHERE department_id = :dept AND role = 'dept_manager' AND is_active "
        "UNION "
        "SELECT u.id::text FROM users u JOIN user_roles ur ON ur.user_id = u.id "
        " WHERE ur.role_code = 'dept_manager' AND u.department_id = :dept AND u.is_active "
        "LIMIT 1"
    ), {"dept": str(agr.department_id)})).scalar_one_or_none()
    if mgr_id:
        return uuid.UUID(mgr_id), "dept_manager"
    return None, None


async def create_confirm_task(
    db: AsyncSession, agr: PurchaseAgreement, row: AgreementPaymentSchedule
) -> None:
    """认领成功后派履约确认任务。

    这是普通任务,不是审批流 —— 不进 workflow_defs,不需要新的 action key。
    期次塞在 document_number 里而不是给 tasks 加列:tasks 被三个服务镜像。
    """
    assignee_id, assigned_role = await _confirm_assignee(db, agr)
    if assignee_id is None:
        # 没有 owner、也没有(哪怕算上附加角色)在职部门经理可指派。这里不能像
        # 别处一样退回"assigned_role='dept_manager' + assigned_user_id=None"的
        # 角色广播 —— task.py::get_for_role 里 dept_manager 不在
        # _PERSONAL_APPROVAL_ROLES 排除名单里,一条 NULL-assignee 的 dept_manager
        # 任务会广播进**全公司**每个 dept_manager 的收件箱,这既不精确又会把无关
        # 部门的经理拖进来(该文件另一处 _PERSONAL_APPROVAL_ROLES 的注释描述的正
        # 是这同一类"跨部门任务泄漏")。所以宁可不广播:改存一个没人会在
        # broadcast_roles 里撞上的 role("system_admin"——system_admin 调
        # get_for_role 本来就不做角色过滤,总能看到全部任务),同时把这个"没人
        # 能确认"的状态大声报出来——不能悄悄吞掉,否则这一期就会卡在"未确认"
        # 永远付不出去,而没人知道是为什么。
        logger.error(
            "confirm_period task for agreement %s (id=%s) period %s has no "
            "resolvable assignee (%s) — created unassigned; visible only to "
            "system_admin until manually reassigned",
            agr.number, agr.id, row.period_label,
            "no owner_id and no department_id" if not agr.department_id
            else "no owner_id and no active dept_manager (base role or grant) "
                 "in its department",
        )
        from app.services import notification as notification_service
        notification_service.fire_and_forget_admin_alert(
            f"[EPMS] Confirm-service task for {agr.number} has no assignee",
            (
                f"Agreement <b>{agr.number}</b> just claimed an invoice against "
                f"period <b>{row.period_label}</b>, but no one could be assigned to "
                f"confirm the service was delivered — it has no owner, and its "
                f"department has no active department manager (by base role or "
                f"granted role). Payment cannot be raised for this period until "
                f"someone confirms it, and right now nobody has been asked to. "
                f"Please set an owner on the agreement (or an active department "
                f"manager for its department), then confirm the period manually."
            ),
        )
        assigned_role = "system_admin"

    db.add(Task(
        type="confirm_period",
        priority="normal",
        document_type="agr",
        document_id=agr.id,
        document_number=f"{agr.number} · {row.period_label}",
        assigned_role=assigned_role,
        assigned_user_id=assignee_id,
        title=f"Confirm service for {row.period_label}: {agr.title}",
        description=(
            f"An invoice has been matched to {agr.number} for {row.period_label}. "
            "Confirm the service was delivered as expected — payment cannot be "
            "raised until this is confirmed."
        ),
        amount=row.expected_amount,
        vendor=agr.vendor_name,
    ))
    await db.flush()


async def reassign_open_confirm_tasks(
    db: AsyncSession, agr: PurchaseAgreement
) -> int:
    """协议责任人变了以后,把还没做完的履约确认任务改派给新责任人。

    create_confirm_task 是在发票认领某一期的**那一刻**解析责任人的,之后再改
    owner 不会回头动已经建好的任务 —— 于是「把 owner 改成 X」这个动作看上去
    没生效:X 打开协议页依然没有 Confirm 按钮(前端按「我持有这条任务」渲染,
    components/agreements/ScheduleTable.tsx),而旧责任人、或者那条只有
    system_admin 看得见的兜底任务,还占着一条本该易主的待办。

    走 _confirm_assignee 而不是直接写 agr.owner_id:「谁被指派」在建任务和改派
    两条路上必须是同一个口径 —— 包括 owner 被清空后回落到部门经理、以及两者都
    没有时那条 system_admin 兜底。

    不发邮件,和 create_confirm_task 的无人可派告警不同:那里是自动流程,没人在
    看;这里是有人正在管理台上手动改这条协议,结果当场就在他眼前。

    Returns 实际改动了几条。已完成的任务一律不碰 —— 那是历史记录,不是待办。
    """
    assignee_id, assigned_role = await _confirm_assignee(db, agr)
    if assignee_id is None:
        # 同 create_confirm_task:不做 dept_manager 角色广播(会漏进全公司每个
        # 部门经理的收件箱),改存一个没人会在 broadcast_roles 里撞上的 role。
        assigned_role = "system_admin"
        logger.warning(
            "agreement %s (id=%s) now has no resolvable confirm assignee — its "
            "open confirm_period task(s) fall back to system_admin visibility",
            agr.number, agr.id,
        )
    tasks = (await db.execute(
        select(Task).where(Task.document_type == "agr", Task.document_id == agr.id,
                           Task.type == "confirm_period",
                           Task.is_completed.is_(False))
    )).scalars().all()
    changed = 0
    for t in tasks:
        if t.assigned_user_id == assignee_id and t.assigned_role == assigned_role:
            continue
        t.assigned_user_id = assignee_id
        t.assigned_role = assigned_role
        changed += 1
    if changed:
        await db.flush()
    return changed


async def confirm_period(
    db: AsyncSession, agr: PurchaseAgreement, row_id: uuid.UUID, user_id: uuid.UUID
) -> AgreementPaymentSchedule:
    row = (await db.execute(
        select(AgreementPaymentSchedule).where(AgreementPaymentSchedule.id == row_id)
    )).scalar_one_or_none()
    if row is None or row.agreement_id != agr.id:
        raise ValueError("That schedule row does not belong to this agreement")
    if row.schedule_type != "period":
        raise ValueError(
            "Milestone stages are accepted in a later phase, not confirmed here")
    if row.invoice_id is None:
        raise ValueError(
            f"No invoice has been matched to {row.period_label} yet — there is "
            "nothing to confirm")
    if row.accepted_at is not None:
        raise ValueError(f"{row.period_label} has already been confirmed")

    row.accepted_by = user_id
    row.accepted_at = datetime.now(timezone.utc)

    doc_number = f"{agr.number} · {row.period_label}"
    tasks = (await db.execute(
        select(Task).where(Task.document_type == "agr", Task.document_id == agr.id,
                           Task.type == "confirm_period",
                           Task.document_number == doc_number,
                           Task.is_completed.is_(False))
    )).scalars().all()
    for t in tasks:
        t.is_completed = True
        t.completed_at = row.accepted_at
        t.completed_by = user_id
    await db.flush()
    return row
