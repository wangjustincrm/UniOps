"""Invoice endpoints."""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import BearerToken, CurrentUserPayload, SessionDep, require_permission
from app.core.access_scope import build_scope
from app.crud import invoice as invoice_crud
from app.crud import vendor as vendor_crud
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User
from app.schemas.invoice import (
    AssignMatchRequest,
    DeclineMatchRequest,
    InvoiceCreate,
    InvoiceExceptionRequest,
    InvoiceListResponse,
    InvoiceMatchRequest,
    InvoiceResponse,
    InvoiceUpdate,
    MatchReviewRequest,
)
from app.schemas.po import PoListResponse, PoResponse
from app.services.notification import dispatch_task_notification, fire_and_forget_notify
from app.services import finance_client
from app.services import finance_sync
from app.services import tax_prefill

router = APIRouter(prefix="/invoices", tags=["invoices"])

# Kept for business logic (visibility / task-assignment fallback below) — NOT
# the gate anymore. `ApDep` migrated to the shared authz package
# (epms.invoice.match); this tuple still answers "is this caller AP staff
# regardless of an open task assignment" at lines using `_AP_ROLES` below.
_AP_ROLES = ("system_admin", "ap_clerk", "finance_manager", "finance_bp")
ApDep = Annotated[dict, Depends(require_permission("epms.invoice.match"))]
InvoiceUploadDep = Annotated[dict, Depends(require_permission("invoice_upload"))]


async def _attach_match_assignees(db, invoices: list) -> None:
    """Inject match_assignee_id / match_assignee_name onto ORM invoice instances."""
    ids = [inv.id for inv in invoices]
    if not ids:
        return
    rows = (await db.execute(
        select(Task.document_id, Task.assigned_user_id, User.full_name)
        .join(User, User.id == Task.assigned_user_id, isouter=True)
        .where(
            Task.type == "match_invoice",
            Task.document_type == "invoice",
            Task.document_id.in_(ids),
            Task.is_completed.is_(False),
        )
    )).all()
    by_doc = {r[0]: (r[1], r[2]) for r in rows}
    for inv in invoices:
        assignee = by_doc.get(inv.id)
        inv.match_assignee_id = assignee[0] if assignee else None
        inv.match_assignee_name = assignee[1] if assignee else None


async def _has_open_match_task(db, user_id: uuid.UUID, invoice_id: uuid.UUID) -> bool:
    """Return True if the user has an open match_invoice task for this invoice."""
    row = (await db.execute(select(Task.id).where(
        Task.type == "match_invoice",
        Task.document_type == "invoice",
        Task.document_id == invoice_id,
        Task.assigned_user_id == user_id,
        Task.is_completed.is_(False),
    ))).scalar_one_or_none()
    return row is not None


async def _notify_requester_create_pa(db, invoice) -> None:
    """
    After an invoice is matched to a PO, find the requester of the linked PR
    and create a create_pa task for them (if one doesn't already exist for this PO).
    """
    import logging
    logger = logging.getLogger(__name__)

    if not invoice.po_id:
        return

    # Fetch PO to get pr_id and po_number
    po_result = await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == invoice.po_id))
    po = po_result.scalar_one_or_none()
    if po is None or not po.pr_id:
        return

    # Fetch PR to get requester
    pr_result = await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == po.pr_id))
    pr = pr_result.scalar_one_or_none()
    if pr is None:
        return

    requester_id = pr.created_by

    # Avoid duplicate: check if a pending create_pa task already exists for this PO
    existing_result = await db.execute(
        select(Task).where(
            Task.type == "create_pa",
            Task.document_type == "po",
            Task.document_id == invoice.po_id,
            Task.is_completed.is_(False),
        )
    )
    existing_task = existing_result.scalar_one_or_none()
    if existing_task is not None:
        # Task already exists — re-notify with the new invoice number
        fire_and_forget_notify(existing_task, db, extra_vars={"invoice_number": invoice.internal_ref})
        return

    # Create the task
    task = Task(
        type="create_pa",
        priority="normal",
        document_type="po",
        document_id=invoice.po_id,
        document_number=po.number,
        assigned_role="requester",
        assigned_user_id=requester_id,
        title=f"Create Payment Application for {po.number}",
        description=(
            f"Invoice {invoice.internal_ref} has been matched to PO {po.number}. "
            f"Please create a Payment Application to proceed with vendor payment."
        ),
        vendor=po.vendor_name,
        amount=po.total,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)

    fire_and_forget_notify(task, db, extra_vars={"invoice_number": invoice.internal_ref})


@router.get("", response_model=InvoiceListResponse)
async def list_invoices(
    db: SessionDep,
    user: CurrentUserPayload,
    status: str | None = Query(default=None),
    vendor_id: uuid.UUID | None = Query(default=None),
    po_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, le=200),
):
    scope = await build_scope(db, user)
    # Access Control Matrix gate — return empty when view_invoice is disabled.
    if not scope["perms"].get("view_invoice", False):
        return InvoiceListResponse(items=[], total=0)
    # own_uploads/task_user_id only WIDEN an already-restricted scope (OR-ed into
    # the PO-scope conditions in get_all). When the user is unrestricted
    # (po_subq=None, restrict=False) they must see ALL invoices — gating both on
    # restrict prevents own_uploads from collapsing the OR into
    # "uploaded_by == me" and hiding everyone else's invoices from a
    # requester-base user who also holds an unrestricted role (e.g. requester +
    # procurement_manager). task_user_id was already gated; own_uploads was not.
    own_uploads = scope["user_id"] if (scope["role"] == "requester" and scope["restrict"]) else None
    task_uid = uuid.UUID(user["sub"]) if scope["restrict"] else None
    items, total = await invoice_crud.get_all(
        db, status=status, vendor_id=vendor_id, po_id=po_id, search=search,
        po_ids_subq=scope["po_subq"],
        own_uploads_user_id=own_uploads,
        task_user_id=task_uid,
        page=page, page_size=page_size,
    )
    await _attach_match_assignees(db, items)
    return InvoiceListResponse(items=items, total=total)


@router.post("", response_model=InvoiceResponse, status_code=201)
async def upload_invoice(body: InvoiceCreate, db: SessionDep, user: InvoiceUploadDep, token: BearerToken):
    vendor = await vendor_crud.get_by_id(db, body.vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    inv = await invoice_crud.create(
        db, body, vendor_name=vendor.name, uploaded_by=uuid.UUID(user["sub"])
    )
    await tax_prefill.apply_tax_prefill(db, inv, token)   # 新增
    await finance_sync.sync_ap_invoice(db, inv, token)
    return inv


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(invoice_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    scope = await build_scope(db, user)
    # Access Control Matrix gate — treat absence of view_invoice as not-found.
    if not scope["perms"].get("view_invoice", False):
        raise HTTPException(status_code=404, detail="Invoice not found")
    if scope["restrict"]:
        caller_id = uuid.UUID(user["sub"])
        if await _has_open_match_task(db, caller_id, invoice_id):
            await _attach_match_assignees(db, [inv])
            return inv
        # Matcher retention: the person who performed the match retains detail visibility
        # even after their task is completed (you can see what you acted on).
        if inv.matched_by == caller_id:
            await _attach_match_assignees(db, [inv])
            return inv
        if not await invoice_crud.is_visible(db, inv, scope):
            raise HTTPException(status_code=404, detail="Invoice not found")
    await _attach_match_assignees(db, [inv])
    return inv


@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceUpdate,
    db: SessionDep,
    user: InvoiceUploadDep,
    token: BearerToken,
):
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status not in ("unmatched", "exception", "matched"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot edit invoice in '{inv.status}' status"
        )
    # Capture existing PO/GR links before update so we can re-match after
    prev_po_id = inv.po_id
    prev_gr_ids: list[uuid.UUID] = []
    if inv.gr_ids:
        prev_gr_ids = [uuid.UUID(gid) for gid in inv.gr_ids]
    elif inv.gr_id:
        prev_gr_ids = [inv.gr_id]

    result = await invoice_crud.update(db, inv, body)

    # Re-run match against current allocations so variance/status reflect edits.
    if prev_po_id is not None or inv.gr_ids:
        result = await invoice_crud.rematch_from_existing(db, result, matched_by=uuid.UUID(user["sub"]))

    # Sync the (possibly rematched) invoice to finance once: draft if still
    # unmatched/exception, posted if matched. Fail-open.
    await finance_sync.sync_ap_invoice(db, result, token)

    return result


@router.post("/{invoice_id}/match", response_model=InvoiceResponse)
async def match_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceMatchRequest,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    caller_id = uuid.UUID(user["sub"])
    is_ap = user.get("role") in _AP_ROLES
    if not is_ap and not await _has_open_match_task(db, caller_id, invoice_id):
        raise HTTPException(status_code=403, detail="Not allowed to match this invoice")
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status not in ("unmatched", "exception"):
        raise HTTPException(status_code=409, detail=f"Invoice already in status '{inv.status}'")
    require_review = not is_ap
    from app.crud.invoice import AllocationImbalance, LegacyMatchUnsupported, FeeOnlyLinkRequired
    try:
        result = await invoice_crud.match(db, inv, body, matched_by=caller_id,
                                          require_review=require_review)

        # 完成调用者的 match 任务(指派场景)
        now_ts = datetime.now(timezone.utc)
        my_task = (await db.execute(select(Task).where(
            Task.type == "match_invoice", Task.document_type == "invoice",
            Task.document_id == inv.id, Task.assigned_user_id == caller_id,
            Task.is_completed.is_(False),
        ))).scalar_one_or_none()
        reviewer_id = None
        if my_task is not None:
            my_task.is_completed = True
            my_task.completed_at = now_ts
            my_task.completed_by = caller_id
            reviewer_id = my_task.created_by

        # Complete any OTHER open match_invoice tasks for this invoice (e.g. AP matched
        # directly while an assignee still had an open task — no orphans left behind).
        other_open_tasks = (await db.execute(select(Task).where(
            Task.type == "match_invoice", Task.document_type == "invoice",
            Task.document_id == inv.id, Task.assigned_user_id != caller_id,
            Task.is_completed.is_(False),
        ))).scalars().all()
        for other in other_open_tasks:
            other.is_completed = True
            other.completed_at = now_ts
            other.completed_by = caller_id

        if result.status == "match_review" and my_task is not None:
            review = Task(
                type="review_match", priority="normal",
                document_type="invoice", document_id=inv.id,
                document_number=inv.internal_ref,
                assigned_role="ap_clerk",
                assigned_user_id=reviewer_id,
                created_by=caller_id,
                title=f"Review match variance on invoice {inv.internal_ref}",
                description=(
                    f"Invoice {inv.internal_ref} was matched with a non-zero variance "
                    f"({result.variance}). Please review and approve or reject."
                ),
                vendor=inv.vendor_name, amount=inv.total_amount,
            )
            db.add(review)
            await db.flush()
            await db.refresh(review)
            fire_and_forget_notify(review, db, extra_vars={"invoice_number": inv.internal_ref})

        if result.status == "matched":
            # A reference-only (fee-only) match produces zero InvoicePoAllocation
            # rows — that's the clean signal to skip the create_pa notification.
            # Before this feature a "matched" invoice always had >=1 allocation,
            # so this check changes nothing on the pre-existing PO-allocation path.
            from app.models.invoice_allocation import InvoicePoAllocation
            has_alloc = (await db.execute(
                select(InvoicePoAllocation.id)
                .where(InvoicePoAllocation.invoice_id == result.id).limit(1)
            )).first() is not None
            if has_alloc:
                await _notify_requester_create_pa(db, result)

        # Sync to finance: posted if matched, draft otherwise. Fail-open.
        await finance_sync.sync_ap_invoice(db, result, token)

        await _attach_match_assignees(db, [result])
        return result
    except (AllocationImbalance, LegacyMatchUnsupported, FeeOnlyLinkRequired) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# 发票可匹配的 PO 状态(与前端 MATCHABLE_PO_STATUSES 一致)。
# closed 不在其中:已关闭(含 PMS 导入按 PAID 收口)的 PO 不再进入候选(2026-07-10)。
_MATCHABLE_PO_STATUSES = ("issued", "approved", "partially_received", "fully_received")


@router.get("/{invoice_id}/match-candidates", response_model=PoListResponse)
async def list_match_candidates(
    invoice_id: uuid.UUID,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """该发票可分摊的候选 PO(同 vendor、开放状态)。按【发票的匹配权限】授权,
    不走通用 PO scope — 否则没有相关 PR 的被指派人一个候选都看不到(死锁)。"""
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    caller_id = uuid.UUID(user["sub"])
    if user.get("role") not in _AP_ROLES and not await _has_open_match_task(db, caller_id, invoice_id):
        raise HTTPException(status_code=403, detail="Not allowed to match this invoice")

    pos = list((await db.execute(
        select(PurchaseOrder)
        .where(PurchaseOrder.vendor_id == inv.vendor_id,
               PurchaseOrder.status.in_(_MATCHABLE_PO_STATUSES))
        .order_by(PurchaseOrder.number)
    )).scalars().all())

    # 每条 PO line 被【其他发票】累计分摊的税前额 —— 前端据此显示真实 Remaining。
    # 排除当前发票自身(重匹配时它的旧分摊会整体重建,不应占额度)。
    if pos:
        from sqlalchemy import func as sa_func
        from app.models.invoice_allocation import InvoicePoAllocation
        sums = dict((await db.execute(
            select(InvoicePoAllocation.po_line_id,
                   sa_func.sum(InvoicePoAllocation.allocated_amount))
            .where(InvoicePoAllocation.po_id.in_([p.id for p in pos]),
                   InvoicePoAllocation.po_line_id.isnot(None),
                   InvoicePoAllocation.invoice_id != invoice_id)
            .group_by(InvoicePoAllocation.po_line_id)
        )).all())
        for po in pos:
            for li in po.line_items:
                li.already_allocated = sums.get(li.id)
    return PoListResponse(
        items=[PoResponse.model_validate(po) for po in pos],
        total=len(pos),
    )


@router.post("/{invoice_id}/decline-match", response_model=InvoiceResponse)
async def decline_match(
    invoice_id: uuid.UUID,
    body: DeclineMatchRequest,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """被指派人退回匹配指派(必填原因):任务弹回给指派人(created_by)并通知,
    发票保持 unmatched。没有退回路径时,不熟悉该供应商的被指派人会卡死。"""
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if not body.note.strip():
        raise HTTPException(status_code=422, detail="A note is required when declining")
    caller_id = uuid.UUID(user["sub"])
    task = (await db.execute(select(Task).where(
        Task.type == "match_invoice",
        Task.document_type == "invoice",
        Task.document_id == inv.id,
        Task.assigned_user_id == caller_id,
        Task.is_completed.is_(False),
    ))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=403, detail="No active match assignment for you on this invoice")

    caller_name = (await db.execute(
        select(User.full_name).where(User.id == caller_id)
    )).scalar_one_or_none()

    # 弹回指派人;指派人缺失(历史数据)则退给 ap_clerk 角色池
    if task.created_by is not None:
        task.assigned_user_id = task.created_by
        task.assigned_role = "assigned"
    else:
        task.assigned_user_id = None
        task.assigned_role = "ap_clerk"
    task.title = f"Match invoice {inv.internal_ref} to PO (assignment declined)"
    task.description = (
        f"{caller_name or 'The assignee'} declined this match assignment: {body.note.strip()} "
        f"Please match the invoice yourself or reassign it."
    )
    await db.flush()
    await db.refresh(task)
    # Commit the (re)assignment BEFORE notifying: fire_and_forget_notify re-reads
    # the task in a fresh session and resolves the recipient from the committed
    # assigned_user_id. Without this commit the background reader races the
    # request's own commit and, on reassignment, sees the PREVIOUS assignee —
    # emailing the wrong person while the UI badge shows the new one.
    await db.commit()
    fire_and_forget_notify(task, db, extra_vars={"invoice_number": inv.internal_ref})
    await _attach_match_assignees(db, [inv])
    return inv


@router.post("/{invoice_id}/assign-match", response_model=InvoiceResponse)
async def assign_match(
    invoice_id: uuid.UUID,
    body: AssignMatchRequest,
    db: SessionDep,
    user: ApDep,
):
    """AP 将这张发票的 PO 匹配工作指派给某个用户(任何角色)。重复调用=改派。"""
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status not in ("unmatched", "exception"):
        raise HTTPException(status_code=409, detail=f"Invoice already in status '{inv.status}'")

    assignee = await db.get(User, body.user_id)
    if assignee is None or not assignee.is_active:
        raise HTTPException(status_code=404, detail="Assignee not found or inactive")

    assigner_id = uuid.UUID(user["sub"])
    existing = (await db.execute(select(Task).where(
        Task.type == "match_invoice",
        Task.document_type == "invoice",
        Task.document_id == inv.id,
        Task.is_completed.is_(False),
    ))).scalar_one_or_none()

    description = (
        f"You have been assigned to match invoice {inv.internal_ref} "
        f"({inv.vendor_name}, {inv.currency} {inv.total_amount}) to its purchase order(s). "
        f"Open the invoice and allocate its lines to the PO lines."
    )
    if existing is not None:
        existing.assigned_user_id = assignee.id
        existing.created_by = assigner_id
        existing.description = description
        task = existing
    else:
        task = Task(
            type="match_invoice", priority="normal",
            document_type="invoice", document_id=inv.id,
            document_number=inv.internal_ref,
            assigned_role="assigned",            # 非真实角色,防止角色池广播
            assigned_user_id=assignee.id,
            created_by=assigner_id,
            title=f"Match invoice {inv.internal_ref} to PO",
            description=description,
            vendor=inv.vendor_name, amount=inv.total_amount,
        )
        db.add(task)
    await db.flush()
    await db.refresh(task)
    # Commit the (re)assignment BEFORE notifying: fire_and_forget_notify re-reads
    # the task in a fresh session and resolves the recipient from the committed
    # assigned_user_id. Without this commit the background reader races the
    # request's own commit and, on reassignment, sees the PREVIOUS assignee —
    # emailing the wrong person while the UI badge shows the new one.
    await db.commit()
    fire_and_forget_notify(task, db, extra_vars={"invoice_number": inv.internal_ref})
    await _attach_match_assignees(db, [inv])
    return inv


@router.post("/{invoice_id}/match-review", response_model=InvoiceResponse)
async def match_review(
    invoice_id: uuid.UUID,
    body: MatchReviewRequest,
    db: SessionDep,
    user: ApDep,
    token: BearerToken,
):
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if body.action == "reject" and not (body.note or "").strip():
        raise HTTPException(status_code=422, detail="A note is required when rejecting")
    reviewer_id = uuid.UUID(user["sub"])
    try:
        result = await invoice_crud.review_match(db, inv, body.action, body.note, reviewer_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # 完成 open review 任务
    review_task = (await db.execute(select(Task).where(
        Task.type == "review_match", Task.document_type == "invoice",
        Task.document_id == inv.id, Task.is_completed.is_(False),
    ))).scalar_one_or_none()
    prev_assignee = review_task.created_by if review_task else None   # match 者
    if review_task is not None:
        review_task.is_completed = True
        review_task.completed_at = datetime.now(timezone.utc)
        review_task.completed_by = reviewer_id

    if body.action == "reject" and prev_assignee is not None:
        redo = Task(
            type="match_invoice", priority="normal",
            document_type="invoice", document_id=inv.id,
            document_number=inv.internal_ref,
            assigned_role="assigned", assigned_user_id=prev_assignee,
            created_by=reviewer_id,
            title=f"Re-match invoice {inv.internal_ref} to PO",
            description=(
                f"Your match of invoice {inv.internal_ref} was rejected: {body.note} "
                f"Please review the allocation and match again."
            ),
            vendor=inv.vendor_name, amount=inv.total_amount,
        )
        db.add(redo)
        await db.flush()
        await db.refresh(redo)
        fire_and_forget_notify(redo, db, extra_vars={"invoice_number": inv.internal_ref})

    if result.status == "matched":
        await _notify_requester_create_pa(db, result)
    await finance_sync.sync_ap_invoice(db, result, token)
    await _attach_match_assignees(db, [result])
    return result


@router.delete("/{invoice_id}", status_code=204)
async def delete_invoice(
    invoice_id: uuid.UUID,
    db: SessionDep,
    user: InvoiceUploadDep,
    token: BearerToken,
):
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    try:
        await finance_sync.sync_ap_invoice(db, inv, token, void=True)
        # Complete all open match_invoice and review_match tasks before hard delete
        # so no orphaned tasks reference a non-existent invoice.
        caller_id = uuid.UUID(user["sub"])
        now_ts = datetime.now(timezone.utc)
        open_tasks = (await db.execute(select(Task).where(
            Task.type.in_(["match_invoice", "review_match"]),
            Task.document_type == "invoice",
            Task.document_id == invoice_id,
            Task.is_completed.is_(False),
        ))).scalars().all()
        for t in open_tasks:
            t.is_completed = True
            t.completed_at = now_ts
            t.completed_by = caller_id
        await db.flush()
        await invoice_crud.delete(db, inv)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{invoice_id}/exception", response_model=InvoiceResponse)
async def resolve_exception(
    invoice_id: uuid.UUID,
    body: InvoiceExceptionRequest,
    db: SessionDep,
    user: ApDep,
    token: BearerToken,
):
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    try:
        result = await invoice_crud.resolve_exception(db, inv, body, resolved_by=uuid.UUID(user["sub"]))
        # Sync to finance: posted if matched, draft otherwise. Fail-open.
        await finance_sync.sync_ap_invoice(db, result, token)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
