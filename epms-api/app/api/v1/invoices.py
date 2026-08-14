"""Invoice endpoints."""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import BearerToken, CurrentUserPayload, SessionDep, require_permission
from app.core.access_scope import build_scope, is_agreement_visible
from uniops_authz import has_permission
from app.crud import agreement as agreement_crud
from app.crud import agreement_receipt as agreement_receipt_crud
from app.crud import invoice as invoice_crud
from app.crud import vendor as vendor_crud
from app.models.agreement import PurchaseAgreement
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User
from app.schemas.agreement import AgreementListResponse
from app.schemas.agreement_receipt import ReceiptListResponse
from app.schemas.invoice import (
    AssignBillingPeriodRequest,
    AssignMatchRequest,
    DeclineMatchRequest,
    InvoiceCreate,
    InvoiceExceptionRequest,
    InvoiceListResponse,
    InvoiceMatchRequest,
    InvoiceReceiptsRequest,
    InvoiceResponse,
    InvoiceUpdate,
    MatchReviewRequest,
    SettleWithoutReceiptRequest,
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
    (match-candidates, agreement-candidates, and agreement receipts) so all
    three enforce the identical rule instead of hand-rolled copies that can
    silently drift apart (review finding, Task 10 round 2 Finding B: the
    receipt endpoint used to gate on the generic epms.agreement.read instead
    of this, and several roles that can legitimately match an invoice — e.g.
    warehouse_staff, its own uploader — don't hold that permission, so they
    403'd on the receipt list and fell back to the no-evidence settlement
    path, silently bypassing the evidence chain Tasks 1-9 built)."""
    caller_id = uuid.UUID(user["sub"])
    is_uploader = inv.uploaded_by == caller_id
    if (user.get("role") not in _AP_ROLES and not is_uploader
            and not await _has_open_match_task(db, caller_id, inv.id)):
        raise HTTPException(status_code=403, detail="Not allowed to match this invoice")


def build_exception_task(inv, reason: str | None, actor_id: uuid.UUID | None) -> Task:
    """Construct (but do not persist/commit/notify) the resolve_exception
    AP-pool task for an invoice.

    Pulled out of _sync_exception_task so the one-shot backfill script
    (scripts/backfill_exception_tasks.py — invoices that reached
    status='exception' before this feature existed and so never got a task)
    can build the exact same task shape instead of a second hand-rolled copy
    that would drift out of sync with the live path.
    """
    return Task(
        type="resolve_exception", priority="normal",
        document_type="invoice", document_id=inv.id,
        document_number=inv.internal_ref,
        # 角色池(无指派人)——与 review_match 同理,这样才能命中共享邮箱。
        assigned_role="ap_clerk",
        created_by=actor_id,
        title=f"Resolve match exception — {inv.internal_ref}",
        description=(
            f"Invoice {inv.internal_ref} could not be matched within tolerance: "
            f"{reason} Please resolve the exception or return "
            f"the invoice to the supplier."
        ),
        vendor=inv.vendor_name, amount=inv.total_amount,
    )


async def _sync_exception_task(db, inv, result, actor_id: uuid.UUID) -> None:
    """Open (or close) the resolve_exception AP task for an invoice, given the
    outcome of a match/review action.

    Whole-branch review (finding 3): this used to live inline in the direct
    match_invoice() endpoint only. A delegate's over-tolerance match routes
    through match_review status first (require_review gate) — when AP later
    confirms it in the review panel, review_match() flips the status to
    "exception" same as a direct match would, but nothing downstream ever
    raised the task, so the delegate → AP-confirms → exception path (the
    scenario this branch exists to serve) landed in exception silently. Both
    call sites now share this helper so neither can drift out of sync again.
    """
    now_ts = datetime.now(timezone.utc)
    existing_exc_task = (await db.execute(select(Task).where(
        Task.type == "resolve_exception", Task.document_type == "invoice",
        Task.document_id == inv.id, Task.is_completed.is_(False),
    ))).scalar_one_or_none()
    if result.status == "exception":
        if existing_exc_task is None:
            exc_task = build_exception_task(inv, result.exception_reason, actor_id)
            db.add(exc_task)
            await db.flush()
            await db.refresh(exc_task)
            # fire_and_forget_notify 的后台协程用**新 session** 按 id 读这条任务,
            # 所以必须先提交,否则它读不到、静默 return(与 assign_match 同因同修)。
            await db.commit()
            fire_and_forget_notify(exc_task, db, extra_vars={"invoice_number": inv.internal_ref})
    elif existing_exc_task is not None:
        existing_exc_task.is_completed = True
        existing_exc_task.completed_at = now_ts
        existing_exc_task.completed_by = actor_id


async def _on_invoice_matched(db, invoice) -> None:
    """发票 match 后,对**每一个**被这张发票买单的 PO 按是否达成 3-way 分流:
    - 已收货 → create_pa 任务(Requester)
    - 未收货 → confirm_receipt 催收货(物理→warehouse_staff 池 / 服务→Requester)

    一票多 PO 时表头 Invoice.po_id 只是其中一个;其余 PO 靠
    invoice_po_allocations 挂上来,同样要拿到任务,否则它们的货款没人提付款
    申请(生产 PO-400-2607-12)。3-way 判定统一走
    crud.po.po_has_three_way_matched_invoice —— 对表头 PO 等价于原来的
    `invoice.gr_id is not None`,对分摊 PO 则要求该 PO 自己有 GR。
    """
    from app.crud.po import po_has_three_way_matched_invoice
    from app.models.invoice_allocation import InvoicePoAllocation
    from app.schemas.gr import is_physical

    po_ids: list[uuid.UUID] = []
    if invoice.po_id:
        po_ids.append(invoice.po_id)
    for pid in (await db.execute(
        select(InvoicePoAllocation.po_id)
        .where(InvoicePoAllocation.invoice_id == invoice.id).distinct()
    )).scalars().all():
        if pid not in po_ids:
            po_ids.append(pid)
    if not po_ids:
        return

    for po_id in po_ids:
        po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == po_id)
        )).scalar_one_or_none()
        if po is None:
            continue
        pr = None
        if po.pr_id:
            pr = (await db.execute(
                select(PurchaseRequest).where(PurchaseRequest.id == po.pr_id)
            )).scalar_one_or_none()

        three_way = (invoice.status == "matched"
                     and await po_has_three_way_matched_invoice(db, po.id))
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
    po_subq = scope["po_subq"]
    # Listing scoped TO ONE AGREEMENT follows the agreement's own read gate, not
    # the invoice module's PO-chain scope.
    #
    # An agreement invoice has po_id NULL — the whole point of the route — so
    # `Invoice.po_id.in_(po_subq)` can never match one, and the two widening
    # conditions beside it (own uploads, own open task) only catch invoices the
    # caller personally handled. A requester, who holds epms.agreement.read and
    # view_invoice in the default matrix, therefore opened an agreement and read
    # "No invoices matched to this agreement yet" while seven were matched to it
    # — with the receipts that reconcile those invoices listed in full higher up
    # the same page. The page did not show less; it asserted something false.
    #
    # Everything else that page renders is gated on epms.agreement.read alone
    # with no row scope: the agreement itself (api/v1/agreements.py), its
    # receipts (api/v1/agreement_receipts.py). The invoice sub-list was the one
    # member inheriting a scope its siblings do not have. This aligns it.
    #
    # The widening is confined to the agreement_id filter: the general invoice
    # list, and every po_id-scoped call, keep the PO-chain scope untouched.
    #
    # Both halves are required. has_permission is WHETHER this caller works
    # with agreements (and it, not scope["perms"], because only it carries the
    # system_admin short-circuit — see its docstring); is_agreement_visible is
    # WHICH ones. The first version of this shipped with only the permission
    # half, which handed every agreement's invoices to any requester — the
    # default matrix grants them epms.agreement.read.
    if agreement_id is not None and await has_permission(
        db, scope["user_id"], scope["role"], "epms.agreement.read"
    ) and await is_agreement_visible(db, agreement_id, scope):
        po_subq = own_uploads = task_uid = None
    items, total = await invoice_crud.get_all(
        db, status=status, vendor_id=vendor_id, po_id=po_id, agreement_id=agreement_id, search=search,
        po_ids_subq=po_subq,
        own_uploads_user_id=own_uploads,
        task_user_id=task_uid,
        page=page, page_size=page_size,
    )
    await _attach_match_assignees(db, items)
    await invoice_crud.attach_claimed_receipts(db, items)
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
            await invoice_crud.attach_claimed_receipts(db, [inv])
            return inv
        # Matcher retention: the person who performed the match retains detail visibility
        # even after their task is completed (you can see what you acted on).
        if inv.matched_by == caller_id:
            await _attach_match_assignees(db, [inv])
            await invoice_crud.attach_claimed_receipts(db, [inv])
            return inv
        # An invoice matched to an agreement is part of that agreement's record,
        # and that record is gated on epms.agreement.read with no row scope —
        # the same rule list_invoices applies to the agreement-scoped listing.
        # Without this the agreement page lists the invoice and clicking it
        # 404s, which trades an empty list for a dead link.
        if inv.agreement_id is not None and await has_permission(
            db, scope["user_id"], scope["role"], "epms.agreement.read"
        ) and await is_agreement_visible(db, inv.agreement_id, scope):
            await _attach_match_assignees(db, [inv])
            await invoice_crud.attach_claimed_receipts(db, [inv])
            return inv
        if not await invoice_crud.is_visible(db, inv, scope):
            raise HTTPException(status_code=404, detail="Invoice not found")
    await _attach_match_assignees(db, [inv])
    await invoice_crud.attach_claimed_receipts(db, [inv])
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
        if my_task is not None:
            my_task.is_completed = True
            my_task.completed_at = now_ts
            my_task.completed_by = caller_id

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
            # longer true for house_account: a match with legacy_settlement
            # =False and legacy_settlement_reason=None told AP reviewers
            # "...as a legacy settlement (no receipt evidence): None" about an
            # invoice that was nothing of the sort — the exact opposite of what
            # happened. The whole point of narrowing legacy_settlement was to
            # make this review panel trustworthy.
            #
            # Whole-branch review (M2): there is deliberately NO
            # "backed by N claimed receipt(s)" branch here. Task 6 made
            # matching pure linkage — _match_to_agreement (crud/invoice.py)
            # releases any held evidence and leaves invoice.receipt_ids NULL
            # for every agreement type, and the house_account branch is a bare
            # `pass` that claims nothing — so `result.receipt_ids` is always
            # falsy by the time this runs. Mounting receipts is a separate act
            # on the invoice detail page (PUT /invoices/{id}/receipts), which
            # never creates a review task. A branch on receipt_ids here would
            # be dead code that reads as if this endpoint could still claim
            # evidence; if mounting ever moves back into /match, add it then.
            if result.match_route == "agreement":
                if result.legacy_settlement:
                    review_description = (
                        f"Invoice {inv.internal_ref} was matched to agreement "
                        f"{result.agreement_number} as a legacy settlement (no receipt "
                        f"evidence): {result.legacy_settlement_reason}. Please confirm the "
                        "linkage is correct. Approval of the payment amount happens later, "
                        "on the Payment Application approval chain."
                    )
                elif result.schedule_id is not None:
                    # recurring (auto-claimed or an explicit req.schedule_id)
                    # or milestone: claimed a real billing-schedule row —
                    # neither a legacy settlement nor receipt-backed, so say
                    # nothing that isn't true of both.
                    review_description = (
                        f"Invoice {inv.internal_ref} was matched to agreement "
                        f"{result.agreement_number} against a billing schedule row. "
                        "Please confirm the linkage is correct. Approval of the payment "
                        "amount happens later, on the Payment Application approval chain."
                    )
                else:
                    # Review fix (Important #2 follow-up, Task 5 round 2):
                    # recurring's FIFO auto-claim can legitimately come up
                    # empty (no pending/overdue row, or the amount is out of
                    # tolerance) — that's the ONLY way this branch used to be
                    # reached with schedule_id still None, and it's exactly why
                    # require_review got set. Nothing was claimed, so "against
                    # a billing schedule row" would be the same shape of lie
                    # Important #2 just fixed, just without the literal
                    # "None". The reviewer's actual job here isn't a plain
                    # approve/reject — it's to manually assign which billing
                    # period this invoice covers (the req.schedule_id escape
                    # hatch), so the description has to say that instead.
                    #
                    # Task 6 addendum: matching a house_account invoice to an
                    # agreement no longer sets legacy_settlement or
                    # receipt_ids at all — that used to be impossible (every
                    # house_account match set one or the other), so this
                    # branch was recurring-only. A delegate's plain
                    # house_account match now lands here too, and the
                    # recurring wording above ("no billing period could be
                    # auto-claimed... manually assign the billing period")
                    # would be nonsense for a house_account invoice, which has
                    # no billing periods at all. Distinguish by the
                    # agreement's type, not by what got claimed.
                    agr_type = (await db.execute(
                        select(PurchaseAgreement.agreement_type).where(
                            PurchaseAgreement.id == inv.agreement_id)
                    )).scalar_one_or_none()
                    if agr_type == "house_account":
                        review_description = (
                            f"Invoice {inv.internal_ref} was matched to agreement "
                            f"{result.agreement_number}. No receipt evidence or "
                            "no-evidence declaration has been recorded for it yet. "
                            "Please confirm the linkage is correct. Approval of the "
                            "payment amount happens later, on the Payment Application "
                            "approval chain."
                        )
                    else:
                        review_description = (
                            f"Invoice {inv.internal_ref} was matched to agreement "
                            f"{result.agreement_number}, but no billing period could be "
                            "auto-claimed (none pending, or the amount is outside "
                            "tolerance). Please review and manually assign the billing "
                            "period this invoice covers. Approval of the payment amount "
                            "happens later, on the Payment Application approval chain."
                        )
            else:
                # Whole-branch review (finding 5): don't open with the money
                # figure — that's what read as "approve this payment
                # difference" and caused AP to refuse the task in the first
                # place. Lead with the linkage-confirmation framing, same as
                # every agreement-route description above; the variance is
                # supporting detail, not the headline.
                review_description = (
                    f"Please confirm invoice {inv.internal_ref} is linked to the correct "
                    f"purchase order and goods receipt (variance {result.variance} vs PO "
                    "reference). Approval of the payment amount happens later, on the "
                    "Payment Application approval chain."
                )
            review = Task(
                type="review_match", priority="normal",
                document_type="invoice", document_id=inv.id,
                document_number=inv.internal_ref,
                # 角色池,不钉个人:钉住 assigned_user_id 会让 notification.py
                # 只发派单者一人,并跳过 AP 共享邮箱分支(它只在无指派人时才查)。
                assigned_role="ap_clerk",
                created_by=caller_id,
                title=f"Confirm invoice match — {inv.internal_ref}",
                description=review_description,
                vendor=inv.vendor_name, amount=inv.total_amount,
            )
            db.add(review)
            await db.flush()
            await db.refresh(review)
            # fire_and_forget_notify 的后台协程用**新 session** 按 id 读这条任务,
            # 所以必须先提交,否则它读不到、静默 return(与 assign_match 同因同修)。
            await db.commit()
            fire_and_forget_notify(review, db, extra_vars={"invoice_number": inv.internal_ref})

        # 超容差(exception)任务:开一条 AP 角色池任务;若发票重新匹配后
        # 不再落在 exception(改判 matched/match_review),关掉遗留的开放任务
        # —— 否则会留下一条指向"已不再是 exception"发票的僵尸任务。
        # match_review() 复核落定 exception 时也要走这条路径,故抽成共享
        # helper(见 finding 3)——两处都调用同一份逻辑,不会再各改各的。
        await _sync_exception_task(db, inv, result, caller_id)

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
        await invoice_crud.attach_claimed_receipts(db, [result])
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


@router.get("/{invoice_id}/agreements/{agreement_id}/receipts", response_model=ReceiptListResponse)
async def list_invoice_agreement_receipts(
    invoice_id: uuid.UUID,
    agreement_id: uuid.UUID,
    db: SessionDep,
    user: CurrentUserPayload,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
):
    """Agreement receipts for one of THIS INVOICE's candidate agreements — a
    separate, invoice-scoped route from GET /agreements/{id}/receipts (which
    stays gated on epms.agreement.read for the agreement detail page).

    Review finding (Task 10 round 2, Finding B): the house_account matching
    UI (MatchPanel) used to call the epms.agreement.read-gated route
    directly. That permission is not granted to every role that can
    legitimately match an invoice — warehouse_staff, supervisor, cfo,
    vendor_manager, erp_pa_officer among them — so those callers 403'd on
    the receipt list the instant they picked a house_account agreement, and
    fell back to the legacy no-evidence settlement path with no idea real
    evidence existed. An agreement receipt carries strictly less information
    than the agreement itself, which this same caller can already reach via
    agreement-candidates, so authorising this route the identical way
    (_require_invoice_match_access, shared with match-candidates and
    agreement-candidates — not a parallel copy) is safe: it can only ever
    widen access to something already visible one layer up, and the
    candidates_for_vendor membership check below still stops it from
    becoming "any authenticated user reads any agreement's receipts" —
    agreement_id must be one of the invoice's OWN admissible candidates.
    """
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    await _require_invoice_match_access(db, user, inv)

    candidates = await agreement_crud.candidates_for_vendor(db, inv.vendor_id)
    if not any(agr.id == agreement_id for agr in candidates):
        raise HTTPException(status_code=404, detail="Agreement not found")

    items = await agreement_receipt_crud.list_for_agreement(db, agreement_id, status=status_filter)
    return {"items": items, "total": len(items)}


@router.put("/{invoice_id}/receipts", response_model=InvoiceResponse)
async def set_invoice_receipts(
    invoice_id: uuid.UUID,
    body: InvoiceReceiptsRequest,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """挂凭证是发票详情页上独立于 /match 的一个动作(Task 7 —— 见 Task 6 对
    InvoiceMatchRequest 的拆分)。全量覆盖语义:body.receipt_ids 就是这张
    发票挂载后应持有的完整集合,没列出的会被释放回 open。授权与
    match-candidates / agreement-candidates / 发票范围内的凭证列表同源
    (_require_invoice_match_access),不另抄一份判定。"""
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    await _require_invoice_match_access(db, user, inv)

    try:
        return await invoice_crud.set_receipts(
            db, inv, body.receipt_ids, body.variance_reason)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/{invoice_id}/billing-period", response_model=InvoiceResponse)
async def assign_invoice_billing_period(
    invoice_id: uuid.UUID,
    body: AssignBillingPeriodRequest,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """Link a matched recurring invoice to a billing period after the fact.

    /match takes a schedule_id, but only while the invoice is still unmatched —
    and an invoice that the automatic claim could not place lands in
    match_review with no period, gets approved there without gaining one, and
    then can never be paid ("not linked to a billing period") because /match
    refuses to run twice and the agreement's tolerance is no longer editable.
    This is the way out of that state, authorised exactly like every other
    action on this page (_require_invoice_match_access).
    """
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    await _require_invoice_match_access(db, user, inv)
    try:
        return await invoice_crud.assign_billing_period(db, inv, body.schedule_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/{invoice_id}/settle-without-receipt", response_model=InvoiceResponse)
async def settle_invoice_without_receipt(
    invoice_id: uuid.UUID,
    body: SettleWithoutReceiptRequest,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """显式声明这张发票没有任何签收凭证(Task 7)。释放它可能还持有的凭证 ——
    一张自称无凭证的发票不该继续锁着几份真凭证。授权同 set_invoice_receipts。"""
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    await _require_invoice_match_access(db, user, inv)

    try:
        return await invoice_crud.settle_without_receipt(db, inv, body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


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
    await invoice_crud.attach_claimed_receipts(db, [inv])
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
    # receipts on the agreement and then come back and claim them.
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
            f"agreement, make sure the supporting receipts are recorded, then "
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
    await invoice_crud.attach_claimed_receipts(db, [inv])
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

    # Finding 3: an approve here can land the invoice in "exception" just like
    # a direct match_invoice() call does (over-tolerance variance, confirmed
    # by AP) — raise the same resolve_exception task, via the same helper
    # match_invoice() uses, so the delegate → AP-confirms → exception path
    # doesn't silently skip it. A no-op on reject (status goes to unmatched).
    await _sync_exception_task(db, inv, result, reviewer_id)

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
    await invoice_crud.attach_claimed_receipts(db, [result])
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
        # Complete all open match_invoice / review_match / resolve_exception tasks
        # before hard delete so no orphaned tasks reference a non-existent invoice.
        caller_id = uuid.UUID(user["sub"])
        now_ts = datetime.now(timezone.utc)
        open_tasks = (await db.execute(select(Task).where(
            Task.type.in_(["match_invoice", "review_match", "resolve_exception"]),
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
        exc_task = (await db.execute(select(Task).where(
            Task.type == "resolve_exception", Task.document_type == "invoice",
            Task.document_id == inv.id, Task.is_completed.is_(False),
        ))).scalar_one_or_none()
        if exc_task is not None:
            exc_task.is_completed = True
            exc_task.completed_at = datetime.now(timezone.utc)
            exc_task.completed_by = uuid.UUID(user["sub"])
        # Sync to finance: posted if matched, draft otherwise. Fail-open.
        await finance_sync.sync_ap_invoice(db, result, token)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
