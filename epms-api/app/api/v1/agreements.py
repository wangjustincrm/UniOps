"""Purchase Agreement endpoints."""
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.access_scope import _effective_role_codes
from app.core.authz import require_permission
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.crud import agreement as agr_crud
from app.crud import agreement_schedule as agr_sched_crud
from app.crud import vendor as vendor_crud
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.task import Task
from app.schemas.agreement import (
    AgreementActionRequest,
    AgreementCreate,
    AgreementListResponse,
    AgreementResponse,
    AgreementUpdate,
    ScheduleListResponse,
    ScheduleRowResponse,
)
from app.services.approval_client import delegate_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agreements", tags=["purchase-agreements"])

AgrReadDep = Annotated[dict, Depends(require_permission("epms.agreement.read"))]
AgrWriteDep = Annotated[dict, Depends(require_permission("epms.agreement.write"))]


@router.get("", response_model=AgreementListResponse)
async def list_agreements(
    db: SessionDep,
    user: AgrReadDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    vendor_id: uuid.UUID | None = Query(default=None),
    agreement_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, le=200),
):
    items, total = await agr_crud.get_all(
        db, status=status_filter, vendor_id=vendor_id,
        agreement_type=agreement_type, search=search,
        page=page, page_size=page_size,
    )
    return {"items": items, "total": total}


@router.post("", response_model=AgreementResponse, status_code=status.HTTP_201_CREATED)
async def create_agreement(body: AgreementCreate, db: SessionDep, user: AgrWriteDep):
    vendor = await vendor_crud.get_by_id(db, body.vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return await agr_crud.create(
        db, body, vendor_name=vendor.name, created_by=uuid.UUID(user["sub"]),
    )


@router.get("/{agreement_id}", response_model=AgreementResponse)
async def get_agreement(agreement_id: uuid.UUID, db: SessionDep, user: AgrReadDep):
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return agr


@router.patch("/{agreement_id}", response_model=AgreementResponse)
async def update_agreement(
    agreement_id: uuid.UUID, body: AgreementUpdate, db: SessionDep, user: AgrWriteDep
):
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if agr.status not in agr_crud.EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Agreement is {agr.status}; only a draft agreement can be edited. "
                   "Changing terms after approval requires a new approval round.",
        )
    try:
        return await agr_crud.update(db, agr, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{agreement_id}/action", response_model=AgreementResponse)
async def agreement_action(
    agreement_id: uuid.UUID,
    body: AgreementActionRequest,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    # Gated by the open approval task, not by epms.agreement.write — approval
    # authority comes from the approval engine, mirroring po.py::po_action.
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    try:
        await delegate_action("agr", str(agreement_id), body.action, body.comment, token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    await db.refresh(agr)
    # 排期在这里生成,不在 approval-api 的 _post_approve_agr —— 那样 approval-api
    # 就得镜像 agreement_payment_schedule,为一个纯 EPMS 概念增加跨服务耦合点。
    # 状态机归 approval-api,排期归 EPMS。
    if agr.status == "active":
        try:
            await agr_sched_crud.ensure_period_rows(db, agr)
            await db.commit()
            await db.refresh(agr)
        except ValueError:
            # build_period_rows 理论上不会在这里炸 —— validate_recurrence 在
            # create/update 时已经用同样的入参跑过一次,一份存进库的协议不该
            # 再产出无法生成排期的周期参数。但万一它还是炸了:此时审批已经在
            # approval-api 落地(状态已 active、审批任务已关闭),把 500 扔回
            # 前端只会让调用方误以为审批本身失败了,而它没有。所以这里既不
            # 重新抛出,也不静默吞掉——记一条 error 日志留痕,并且额外发一条
            # admin alert(照抄 gr.py 里 GR 自动完成成异常态时的同一套模式):
            # 一份 active 却没有排期行的循环协议,在所有 UI 上都不可见,而且
            # 永远不会有发票能自动认领到它,光靠日志没人会盯着看。
            logger.error(
                "ensure_period_rows failed for agreement %s (id=%s) after "
                "approval was already recorded as active; schedule not "
                "generated, needs manual resync",
                agr.number, agr.id, exc_info=True,
            )
            from app.services import notification as notification_service
            notification_service.fire_and_forget_admin_alert(
                f"[EPMS] Agreement {agr.number} is active with no payment schedule",
                (
                    f"Recurring agreement <b>{agr.number}</b> was just approved and is now "
                    f"<b>active</b>, but its payment schedule could not be generated "
                    f"(its recurring cycle settings are invalid). No schedule rows exist, "
                    f"so it will not show a schedule on any screen and no invoice will ever "
                    f"auto-claim against it.\n\n"
                    f"Please correct the agreement's cycle settings (recurring type / "
                    f"expected invoice day / anchor month) and trigger a resync to "
                    f"regenerate the schedule."
                ),
            )
    return agr


@router.get("/{agreement_id}/schedule", response_model=ScheduleListResponse)
async def list_schedule(agreement_id: uuid.UUID, db: SessionDep, user: AgrReadDep):
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return {"items": await agr_sched_crud.list_rows(db, agreement_id)}


@router.post("/{agreement_id}/schedule/{row_id}/confirm", response_model=ScheduleRowResponse)
async def confirm_schedule_period(
    agreement_id: uuid.UUID, row_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload
):
    # Owner ruling (Task 7 fix round #2): every other task-driven action in
    # this codebase (POST /tasks/{id}/complete, POST /gr/{id}/action, ...)
    # trusts that holding the task IS the authorization and doesn't re-check
    # the actor. This endpoint deliberately does NOT follow that convention:
    # recurring agreements skip goods receipt entirely, so this confirmation
    # is the ONLY human checkpoint before an invoice pays itself. If anyone
    # authenticated could click it, the control would be decorative — and it
    # would make create_confirm_task's "no resolvable assignee" system_admin
    # fallback unauditable, since an admin would have no way to tell whether
    # the circuit actually worked. Do not "harmonise" this back to match the
    # sibling endpoints; it is intentionally stricter.
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    row = (await db.execute(
        select(AgreementPaymentSchedule).where(AgreementPaymentSchedule.id == row_id)
    )).scalar_one_or_none()
    if row is None or row.agreement_id != agr.id:
        raise HTTPException(status_code=404, detail="Schedule row not found")

    caller_id = uuid.UUID(user["sub"])
    caller_role = user.get("role", "")
    if caller_role != "system_admin":
        # The row exists — 403, not 404: the caller just isn't the one who
        # may confirm it. Matched on document_id + document_number the same
        # way confirm_period() completes the task, so a caller holding some
        # OTHER period's task for this same agreement is refused too.
        task = (await db.execute(
            select(Task).where(
                Task.document_type == "agr", Task.document_id == agr.id,
                Task.type == "confirm_period",
                Task.document_number == f"{agr.number} · {row.period_label}",
                Task.is_completed.is_(False),
            )
        )).scalar_one_or_none()
        if task is None:
            raise HTTPException(
                status_code=403,
                detail=f"There is no open confirmation task for {row.period_label} "
                       "to act on.")
        # Reuses the same base-role ∪ user_roles-grant union _confirm_assignee
        # resolves dept_manager through — "holds the task" must mean the same
        # thing on both sides (who gets assigned vs who may act), or a
        # grant-only dept_manager could be assigned the task and then be
        # unable to complete it.
        effective_roles = await _effective_role_codes(db, caller_role, caller_id)
        holds_it = (
            task.assigned_user_id == caller_id
            or task.assigned_role in effective_roles
        )
        if not holds_it:
            raise HTTPException(
                status_code=403,
                detail=f"Only the person assigned to confirm {row.period_label} "
                       f"(role: {task.assigned_role}) may confirm it.")

    try:
        row = await agr_sched_crud.confirm_period(db, agr, row_id, caller_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    await db.refresh(row)
    return row
