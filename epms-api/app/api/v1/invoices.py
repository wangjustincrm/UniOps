"""Invoice endpoints."""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import BearerToken, CurrentUserPayload, SessionDep, require_permission
from app.core.access_scope import build_scope
from app.crud import agreement as agreement_crud
from app.crud import agreement_slip as agreement_slip_crud
from app.crud import invoice as invoice_crud
from app.crud import vendor as vendor_crud
from app.models.agreement import PurchaseAgreement
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User
from app.schemas.agreement import AgreementListResponse
from app.schemas.agreement_slip import SlipListResponse
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


async def _require_invoice_match_access(db, user: dict, inv) -> None:
    """Authorise by THIS INVOICE's own match permission — AP staff, its
    uploader, or the holder of an open match_invoice task on it — never a
    generic scope, or an assignee with no related PR sees zero candidates
    and deadlocks. Shared by every invoice-scoped candidate endpoint
    (match-candidates, agreement-candidates, and agreement slips) so all
    three enforce the identical rule instead of hand-rolled copies that can
    silently drift apart (review finding, Task 10 round 2 Finding B: the
    slip endpoint used to gate on the generic epms.agreement.read instead of
    this, and several roles that can legitimately match an invoice — e.g.
    warehouse_staff, its own uploader — don't hold that permission, so they
    403'd on the slip list and fell back to the no-evidence settlement path,
    silently bypassing the evidence chain Tasks 1-9 built)."""
    caller_id = uuid.UUID(user["sub"])
    is_uploader = inv.uploaded_by == caller_id
    if (user.get("role") not in _AP_ROLES and not is_uploader
            and not await _has_open_match_task(db, caller_id, inv.id)):
        raise HTTPException(status_code=403, detail="Not allowed to match this invoice")


async def _on_invoice_matched(db, invoice) -> None:
    """发票 match 后按是否达成 3-way 分流:
    - 已挂 GR(gr_id 非空) → create_pa 任务(Requester)
    - 未挂 GR → confirm_receipt 催收货(物理→warehouse_staff 池 / 服务→Requester)
    """
    from app.schemas.gr import is_physical
    if not invoice.po_id:
        return
    po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == invoice.po_id))).scalar_one_or_none()
    if po is None:
        return
    pr = None
    if po.pr_id:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == po.pr_id))).scalar_one_or_none()

    three_way = invoice.status == "matched" and invoice.gr_id is not None
    if three_way:
        await _create_or_renotify_create_pa(db, po, pr, invoice)   # = 原 create_pa 逻辑抽出
    else:
        await _create_or_renotify_confirm_receipt(db, po, pr, invoice, is_physical(po.type))


async def _create_or_renotify_create_pa(db, po, pr, invoice) -> None:
    """
    After an invoice is 3-way matched (has a GR), raise a create_pa task.

    Normal POs route to the linked PR's requester (personal assignee). NC-imported
    POs have no PR/requester, so they route to the erp_pa_officer pool
    (assigned_role + NULL assignee → broadcast to every holder of that role).
    A non-NC PO without a PR still gets no task (unchanged legacy behaviour).
    """
    is_nc_orphan = pr is None and po.source == "nc"
    if pr is None and not is_nc_orphan:   # non-NC PO with no PR → don't build create_pa
        return
    existing = (await db.execute(select(Task).where(
        Task.type == "create_pa", Task.document_type == "po",
        Task.document_id == po.id, Task.is_completed.is_(False),
    ))).scalar_one_or_none()
    if existing is not None:
        fire_and_forget_notify(existing, db, extra_vars={"invoice_number": invoice.internal_ref})
        return
    if is_nc_orphan:
        assigned_role, assigned_user_id = "erp_pa_officer", None
    else:
        assigned_role, assigned_user_id = "requester", pr.created_by
    task = Task(
        type="create_pa", priority="normal", document_type="po",
        document_id=po.id, document_number=po.number,
        assigned_role=assigned_role, assigned_user_id=assigned_user_id,
        title=f"Create Payment Application for {po.number}",
        description=(f"Invoice {invoice.internal_ref} has been matched to PO {po.number}. "
                     f"Please create a Payment Application to proceed with vendor payment."),
        vendor=po.vendor_name, amount=po.total,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    fire_and_forget_notify(task, db, extra_vars={"invoice_number": invoice.internal_ref})


async def _create_or_renotify_confirm_receipt(db, po, pr, invoice, physical: bool) -> None:
    """
    After an invoice is matched to a PO without a GR yet, nudge someone to
    confirm receipt (physical → warehouse_staff pool; service → requester).
    """
    existing = (await db.execute(select(Task).where(
        Task.type == "confirm_receipt",
        Task.document_type == "po",
        Task.document_id == po.id,
        Task.is_completed.is_(False),
    ))).scalar_one_or_none()
    if existing is not None:
        fire_and_forget_notify(existing, db, extra_vars={"invoice_number": invoice.internal_ref})
        return
    if physical:
        assigned_role, assigned_user_id = "warehouse_staff", None
    else:
        assigned_role, assigned_user_id = "requester", (pr.created_by if pr else None)
    task = Task(
        type="confirm_receipt", priority="normal",
        document_type="po", document_id=po.id, document_number=po.number,
        assigned_role=assigned_role, assigned_user_id=assigned_user_id,
        title=f"Confirm goods receipt for {po.number}",
        description=(f"Invoice {invoice.internal_ref} has been matched to PO {po.number} "
                     f"but goods/service is not received yet. Please confirm receipt and create a GR."),
        vendor=po.vendor_name, amount=po.total,
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
    agreement_id: uuid.UUID | None = Query(default=None),
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
        db, status=status, vendor_id=vendor_id, po_id=po_id, agreement_id=agreement_id, search=search,
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
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    is_uploader = inv.uploaded_by == caller_id
    if not is_ap and not is_uploader and not await _has_open_match_task(db, caller_id, invoice_id):
        raise HTTPException(status_code=403, detail="Not allowed to match this invoice")
    if inv.status not in ("unmatched", "exception"):
        raise HTTPException(status_code=409, detail=f"Invoice already in status '{inv.status}'")
    require_review = not (is_ap or is_uploader)
    from app.crud.invoice import (
        AgreementMatchInvalid,
        AllocationImbalance,
        FeeOnlyLinkRequired,
        LegacyMatchUnsupported,
    )
    try:
        # No GR picked in the request → attach the GRs that already received these
        # lines (goods-first, invoice-later). A selection sent by the caller, even
        # an empty one, is honoured verbatim.
        result = await invoice_crud.match(db, inv, body, matched_by=caller_id,
                                          require_review=require_review,
                                          auto_link_grs=body.gr_ids is None and body.gr_id is None)

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
            # Route-aware description: an agreement match's variance is always
            # 0 (there is no PO line to compare against — that's the entire
            # point of the legacy_settlement escape hatch), so the PO route's
            # "non-zero variance (0)" wording would be self-contradictory here.
            #
            # Review fix (Important #2, Task 5 round 1): branch on
            # legacy_settlement rather than assuming every "agreement" route
            # match is a no-evidence legacy settlement. Task 5 made that no
            # longer true for house_account: a match with claimed pickup
            # slips has legacy_settlement=False and legacy_settlement_reason
            # =None, so the old unconditional wording told AP reviewers
            # "...as a legacy settlement (no receipt evidence): None" about
            # an invoice that DOES have receipt evidence — the exact opposite
            # of what happened. The whole point of narrowing legacy_settlement
            # was to make this review panel trustworthy.
            if result.match_route == "agreement":
                if result.legacy_settlement:
                    review_description = (
                        f"Invoice {inv.internal_ref} was matched to agreement "
                        f"{result.agreement_number} as a legacy settlement (no receipt "
                        f"evidence): {result.legacy_settlement_reason}. Please review and "
                        "approve or reject."
                    )
                elif result.slip_ids:
                    review_description = (
                        f"Invoice {inv.internal_ref} was matched to agreement "
                        f"{result.agreement_number} against {len(result.slip_ids)} claimed "
                        "pickup slip(s) as receipt evidence. Please review and approve or "
                        "reject."
                    )
                elif result.schedule_id is not None:
                    # recurring (auto-claimed or an explicit req.schedule_id)
                    # or milestone: claimed a real billing-schedule row —
                    # neither a legacy settlement nor slip-backed, so say
                    # nothing that isn't true of both.
                    review_description = (
                        f"Invoice {inv.internal_ref} was matched to agreement "
                        f"{result.agreement_number} against a billing schedule row. "
                        "Please review and approve or reject."
                    )
                else:
                    # Review fix (Important #2 follow-up, Task 5 round 2):
                    # recurring's FIFO auto-claim can legitimately come up
                    # empty (no pending/overdue row, or the amount is out of
                    # tolerance) — that's the ONLY way this branch is reached
                    # with schedule_id still None, and it's exactly why
                    # require_review got set. Nothing was claimed, so "against
                    # a billing schedule row" would be the same shape of lie
                    # Important #2 just fixed, just without the literal
                    # "None". The reviewer's actual job here isn't a plain
                    # approve/reject — it's to manually assign which billing
                    # period this invoice covers (the req.schedule_id escape
                    # hatch), so the description has to say that instead.
                    review_description = (
                        f"Invoice {inv.internal_ref} was matched to agreement "
                        f"{result.agreement_number}, but no billing period could be "
                        "auto-claimed (none pending, or the amount is outside "
                        "tolerance). Please review and manually assign the billing "
                        "period this invoice covers."
                    )
            else:
                review_description = (
                    f"Invoice {inv.internal_ref} was matched with a non-zero variance "
                    f"({result.variance}). Please review and approve or reject."
                )
            review = Task(
                type="review_match", priority="normal",
                document_type="invoice", document_id=inv.id,
                document_number=inv.internal_ref,
                assigned_role="ap_clerk",
                assigned_user_id=reviewer_id,
                created_by=caller_id,
                title=f"Review match variance on invoice {inv.internal_ref}",
                description=review_description,
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
                await _on_invoice_matched(db, result)

        # Sync to finance: posted if matched, draft otherwise. Fail-open.
        await finance_sync.sync_ap_invoice(db, result, token)

        await _attach_match_assignees(db, [result])
        return result
    except (AgreementMatchInvalid, AllocationImbalance, LegacyMatchUnsupported, FeeOnlyLinkRequired) as exc:
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
    await _require_invoice_match_access(db, user, inv)

    pos = list((await db.execute(
        select(PurchaseOrder)
        .where(PurchaseOrder.vendor_id == inv.vendor_id,
               PurchaseOrder.status.in_(_MATCHABLE_PO_STATUSES))
        .order_by(PurchaseOrder.number)
    )).scalars().all())

    # 每条 PO line 被【其他发票】累计分摊的税前额 —— 前端据此显示真实 Remaining。
    # 排除当前发票自身(重匹配时它的旧分摊会整体重建,不应占额度)。
    if pos:
        from decimal import Decimal
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
        # 每张 PO 被【其他发票】累计分摊的总额(所有 po_line_id 之和)—— 总额匹配模式下
        # 前端据此显示该 PO 的真实 Remaining。
        po_totals = dict((await db.execute(
            select(InvoicePoAllocation.po_id,
                   sa_func.sum(InvoicePoAllocation.allocated_amount))
            .where(InvoicePoAllocation.po_id.in_([p.id for p in pos]),
                   InvoicePoAllocation.invoice_id != invoice_id)
            .group_by(InvoicePoAllocation.po_id)
        )).all())
        for po in pos:
            for li in po.line_items:
                li.already_allocated = sums.get(li.id)
            po.already_allocated_total = po_totals.get(po.id) or Decimal("0")
    return PoListResponse(
        items=[PoResponse.model_validate(po) for po in pos],
        total=len(pos),
    )


@router.get("/{invoice_id}/agreement-candidates", response_model=AgreementListResponse)
async def list_agreement_candidates(
    invoice_id: uuid.UUID,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """Agreements this invoice may be matched to (same vendor, inside the
    admission window). Authorised exactly like list_match_candidates — by the
    invoice's match permission, NOT by a generic agreement scope, or an assignee
    with no related PR sees zero candidates and deadlocks."""
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    await _require_invoice_match_access(db, user, inv)

    items = await agreement_crud.candidates_for_vendor(db, inv.vendor_id)
    return {"items": items, "total": len(items)}


@router.get("/{invoice_id}/agreements/{agreement_id}/slips", response_model=SlipListResponse)
async def list_invoice_agreement_slips(
    invoice_id: uuid.UUID,
    agreement_id: uuid.UUID,
    db: SessionDep,
    user: CurrentUserPayload,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
):
    """Pickup slips for one of THIS INVOICE's candidate agreements — a
    separate, invoice-scoped route from GET /agreements/{id}/slips (which
    stays gated on epms.agreement.read for the agreement detail page).

    Review finding (Task 10 round 2, Finding B): the house_account matching
    UI (MatchPanel) used to call the epms.agreement.read-gated route
    directly. That permission is not granted to every role that can
    legitimately match an invoice — warehouse_staff, supervisor, cfo,
    vendor_manager, erp_pa_officer among them — so those callers 403'd on
    the slip list the instant they picked a house_account agreement, and
    fell back to the legacy no-evidence settlement path with no idea real
    evidence existed. A pickup slip carries strictly less information than
    the agreement itself, which this same caller can already reach via
    agreement-candidates, so authorising this route the identical way
    (_require_invoice_match_access, shared with match-candidates and
    agreement-candidates — not a parallel copy) is safe: it can only ever
    widen access to something already visible one layer up, and the
    candidates_for_vendor membership check below still stops it from
    becoming "any authenticated user reads any agreement's slips" —
    agreement_id must be one of the invoice's OWN admissible candidates.
    """
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    await _require_invoice_match_access(db, user, inv)

    candidates = await agreement_crud.candidates_for_vendor(db, inv.vendor_id)
    if not any(agr.id == agreement_id for agr in candidates):
        raise HTTPException(status_code=404, detail="Agreement not found")

    items = await agreement_slip_crud.list_for_agreement(db, agreement_id, status=status_filter)
    return {"items": items, "total": len(items)}


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

    # Whole-branch review (D): the assignment task's copy was hard-coded PO
    # wording — "Match invoice X to PO" / "allocate its lines to the PO lines"
    # — and told anyone assigned on the agreement route to do something that
    # does not exist there. There are no PO lines on an agreement, and no
    # allocation step; what that person actually has to do is record the
    # pickup slips on the agreement and then come back and claim them.
    # Same defect and same fix as the review_match copy above.
    #
    # The branch is on `inv.agreement_id`, not on match_route or agreement
    # type: assignment is allowed while the invoice is still "unmatched" or
    # "exception", and in that state the invoice usually has NO agreement link
    # yet (AP assigns first, the route is decided later by whoever matches).
    # PO wording is the right default for that genuinely-unknown case — this
    # only re-words the case where the link already exists, i.e. the invoice
    # was matched to an agreement and then knocked back to exception, or AP
    # pre-linked it. No lookup, no guess.
    if inv.agreement_id is not None:
        agr_number = (await db.execute(
            select(PurchaseAgreement.number).where(
                PurchaseAgreement.id == inv.agreement_id))).scalar_one_or_none()
        agr_label = f"agreement {agr_number}" if agr_number else "its agreement"
        title = f"Match invoice {inv.internal_ref} to {agr_label}"
        description = (
            f"You have been assigned to match invoice {inv.internal_ref} "
            f"({inv.vendor_name}, {inv.currency} {inv.total_amount}) to {agr_label}. "
            f"There is no purchase order or goods receipt on this route: open the "
            f"agreement, make sure the supporting pickup slips are recorded, then "
            f"open the invoice and claim them."
        )
    else:
        title = f"Match invoice {inv.internal_ref} to PO"
        description = (
            f"You have been assigned to match invoice {inv.internal_ref} "
            f"({inv.vendor_name}, {inv.currency} {inv.total_amount}) to its purchase order(s). "
            f"Open the invoice and allocate its lines to the PO lines."
        )
    if existing is not None:
        existing.assigned_user_id = assignee.id
        existing.created_by = assigner_id
        existing.description = description
        # Title too, not just the description: a reassignment after the route
        # became known would otherwise keep the stale PO title in the inbox
        # list, which is the only text the assignee sees before opening it.
        existing.title = title
        task = existing
    else:
        task = Task(
            type="match_invoice", priority="normal",
            document_type="invoice", document_id=inv.id,
            document_number=inv.internal_ref,
            assigned_role="assigned",            # 非真实角色,防止角色池广播
            assigned_user_id=assignee.id,
            created_by=assigner_id,
            title=title,
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
        # Route-neutral wording: this task fires on ANY rejected match_review,
        # PO or agreement. "to PO" was accurate before the agreement route
        # could reach this state; review_match() clears match_route on reject,
        # so by here there is no reliable per-route signal left to branch on
        # anyway — the fix is to not need one.
        redo = Task(
            type="match_invoice", priority="normal",
            document_type="invoice", document_id=inv.id,
            document_number=inv.internal_ref,
            assigned_role="assigned", assigned_user_id=prev_assignee,
            created_by=reviewer_id,
            title=f"Re-match invoice {inv.internal_ref}",
            description=(
                f"Your match of invoice {inv.internal_ref} was rejected: {body.note} "
                f"Please match it again."
            ),
            vendor=inv.vendor_name, amount=inv.total_amount,
        )
        db.add(redo)
        await db.flush()
        await db.refresh(redo)
        fire_and_forget_notify(redo, db, extra_vars={"invoice_number": inv.internal_ref})

    if result.status == "matched":
        await _on_invoice_matched(db, result)
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
