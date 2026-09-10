"""Payment Application endpoints — OA module."""
import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import BearerTokenDep, CurrentUserDep, SessionDep
from app.crud import pa as pa_crud
from app.schemas.pa import PaActionRequest, PaDirectCreate, PaDirectUpdate, PaListResponse, PaResponse, PaymentRecord
from app.services.approval_client import delegate_action
from app.services import finance_client, finance_sync

from pydantic import BaseModel

router = APIRouter(prefix="/pa", tags=["payment-applications"])


async def _user_role_codes(db: AsyncSession, user_id: uuid.UUID, base_role: str) -> set[str]:
    """Primary role + additional roles (identity user_roles, same DB). Replaces
    the retired company_config.role_management assignments (phase 3) for can_pay."""
    codes = {base_role} if base_role else set()
    rows = (await db.execute(sa.text(
        "SELECT role_code FROM user_roles WHERE user_id = :u"), {"u": str(user_id)})).scalars().all()
    codes.update(rows)
    return codes


class ApprovalEventOut(BaseModel):
    id: uuid.UUID
    step_idx: int
    action: str
    actor_id: uuid.UUID
    actor_role: str
    comment: str | None
    created_at: str   # ISO string

    model_config = {"from_attributes": True}


@router.get("", response_model=PaListResponse)
async def list_pas(
    db: SessionDep,
    user: CurrentUserDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    po_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: Annotated[int, Query(le=100)] = 20,
):
    """OA PA list — role-based visibility (PRD §B).

    Everyone related to a Direct PA must see it: the Requester (creator) and any
    Approver who participates in its workflow — mirrors the expense-claim list
    contract. The old rule filtered on created_by only, so an approver's list
    went empty the moment they acted. system_admin / ap_clerk see all.
    """
    from sqlalchemy import func, or_, select as sa_select
    from app.api.v1.expenses import _CAN_PAY, _get_workflow_defs
    from app.models.approval_event_mirror import ApprovalEventMirror as AEM
    from app.models.pa import PaymentApplication as PA

    role = user.get("role", "")
    user_id = uuid.UUID(user["sub"])

    # Unrestricted: system_admin (config) and ap_clerk (processes all PA payments, so
    # must see every PA regardless of creator or status — incl. paid).
    if role in ("system_admin", "ap_clerk"):
        items, total = await pa_crud.list_pas(
            db, status=status_filter, po_id=po_id,
            created_by=None, page=page, page_size=page_size,
        )
        return PaListResponse(
            items=[PaResponse.model_validate(p) for p in items],
            total=total,
        )

    # Visible to (a) the creator (Requester) for any status, plus (b) everyone who
    # participates in the PA-DIR approval workflow — any user whose role is a step in
    # workflow_defs["pa_dir"], for all non-draft PAs (not just while it sits at their
    # step), and (c) anyone who personally acted on it (covers role-assignment
    # approvers like Finance BP and any workflow drift) via shared approval_events.
    conditions = [PA.created_by == user_id]

    wf = await _get_workflow_defs(db)
    pa_dir_roles = {s.get("role") for s in (wf.get("pa_dir") or [])}
    if role in pa_dir_roles:
        conditions.append(PA.status != "draft")

    # Pay roles also see approved PAs (payment stage) even when not an approver step.
    if role in _CAN_PAY:
        conditions.append(PA.status == "approved")

    acted_doc_ids = sa_select(AEM.document_id).where(AEM.actor_id == user_id)
    conditions.append(PA.id.in_(acted_doc_ids))

    q = sa_select(PA).where(PA.pa_type == "PA-DIR").where(or_(*conditions))
    if status_filter:
        q = q.where(PA.status == status_filter)
    if po_id:
        q = q.where(PA.po_id == po_id)

    total = (await db.execute(
        sa_select(func.count()).select_from(q.subquery())
    )).scalar_one()
    items = list((await db.execute(
        q.order_by(PA.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())

    return PaListResponse(
        items=[PaResponse.model_validate(p) for p in items],
        total=total,
    )


@router.get("/by-po/{po_id}", response_model=PaListResponse)
async def list_pas_by_po(po_id: uuid.UUID, db: SessionDep, user: CurrentUserDep):
    """Read PAs linked to a specific PO — called by EPMS DocumentChainTree.

    Object-level authz (IDOR fix): filter to PAs the caller may view (owner /
    system_admin / _CAN_PAY / approval participant) — same rule as get_pa.
    """
    items = await pa_crud.get_by_po_id(db, po_id)
    uid = uuid.UUID(user["sub"])
    role = user.get("role", "")
    visible = [p for p in items if await _can_view_pa(db, p, uid, role)]
    return PaListResponse(
        items=[PaResponse.model_validate(p) for p in visible],
        total=len(visible),
    )


@router.post("/direct", response_model=PaResponse, status_code=status.HTTP_201_CREATED)
async def create_direct_pa(
    body: PaDirectCreate,
    db: SessionDep,
    user: CurrentUserDep,
    token: BearerTokenDep,
):
    """Create a PA-DIR (direct payment) linked to a reviewed expense invoice.
    Always requires Finance Manager approval (no dept_manager step).
    """
    from datetime import datetime, timezone
    from decimal import Decimal
    from sqlalchemy import select
    from app.crud._numbering import next_number
    from app.models.invoice import ExpenseInvoice
    from app.models.pa import PaymentApplication

    # Validate invoice. Row-locked for the length of this transaction: the
    # status check and the "mark used" write below are one decision, and two
    # concurrent callers reading `reviewed` would each mint a PA against the
    # same invoice — one vendor bill, paid twice.
    inv = (await db.execute(
        select(ExpenseInvoice)
        .where(ExpenseInvoice.id == body.invoice_id)
        .with_for_update()
    )).scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    # Order matters: "used" is a specific, likelier-than-not reason to be here,
    # and it is also a state that is NOT "reviewed" — checking reviewed first
    # (as this did) made the used branch unreachable dead code and told anyone
    # hitting an already-spent invoice to go review it, which they cannot do.
    if inv.status == "used":
        raise HTTPException(
            status_code=409,
            detail=f"Invoice already linked to {inv.pa_number or 'another PA'}",
        )
    if inv.status != "reviewed":
        raise HTTPException(status_code=409, detail="Invoice must be reviewed (all OCR fields confirmed) before creating a PA")

    # Number allocation. MUST go through next_number (max tail + 1 under a
    # prefix-scoped advisory lock), not count(*)+1: `payment_applications` is
    # shared with epms-api, which mints EPMS PAs from the SAME `PA-YYYYMMDD-`
    # prefix via that helper. count(*) trails the real max the moment either
    # side deletes a row or two allocations overlap, and pa_number is UNIQUE —
    # so the old code handed out an already-taken number and 500'd, which is
    # exactly the defect _numbering.py's docstring was written about.
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    pa_number = await next_number(db, PaymentApplication.pa_number, f"PA-{today}-", width=4)

    title = body.title or f"Direct Payment — {inv.vendor_name or 'Vendor'} {inv.invoice_number or ''}"
    user_id = uuid.UUID(user["sub"])

    pa = PaymentApplication(
        pa_number=pa_number,
        title=title.strip(),
        po_id=None,
        po_number=None,
        vendor_id=body.vendor_id or inv.vendor_id or uuid.UUID('00000000-0000-0000-0000-000000000000'),
        vendor_name=body.vendor_name,
        invoice_ids=[str(body.invoice_id)],
        gr_ids=[],
        pa_type="PA-DIR",
        subtotal=body.payment_amount,
        tax_amount=Decimal("0"),
        shipping_amount=Decimal("0"),
        other_charges=Decimal("0"),
        payment_amount=body.payment_amount,
        currency=body.currency,
        status="draft",
        notes=body.notes,
        budget_account_code=body.budget_account_code,
        cost_center_id=body.cost_center_id,
        approval_step_idx=0,
        created_by=user_id,
    )
    db.add(pa)
    await db.flush()

    # Mark invoice as used
    inv.status = "used"
    inv.pa_id = pa.id
    inv.pa_number = pa_number

    await db.flush()
    await db.refresh(pa)
    await finance_sync.sync_ap_invoice(db, inv, token)
    return PaResponse.model_validate(pa)


@router.patch("/{pa_id}", response_model=PaResponse)
async def patch_direct_pa(
    pa_id: uuid.UUID,
    body: PaDirectUpdate,
    db: SessionDep,
    user: CurrentUserDep,
):
    """Edit a Draft/Returned PA-DIR — owner (or system_admin) only, Payment-Details fields."""
    pa = await pa_crud.get_by_id(db, pa_id)
    if not pa:
        raise HTTPException(status_code=404, detail="PA not found")
    if pa.pa_type != "PA-DIR" or pa.status not in ("draft", "returned"):
        raise HTTPException(status_code=409, detail="Only draft or returned direct PAs can be edited")
    user_id = uuid.UUID(user["sub"])
    if pa.created_by != user_id and user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="Only the owner can edit this PA")

    changes = body.model_dump(exclude_unset=True)
    if "title" in changes:
        title = (changes["title"] or "").strip()
        if title:
            changes["title"] = title
        else:
            changes.pop("title")  # ignore blank title — keep existing

    await pa_crud.update_direct_pa(db, pa, changes)
    return PaResponse.model_validate(pa)


async def _can_view_pa(db: AsyncSession, pa, user_id: uuid.UUID, role: str) -> bool:
    """Object-level authz for PA read endpoints (IDOR fix): a user may view a PA iff
    they are the creator, system_admin, hold a _CAN_PAY role, hold a role that is a
    step in the pa_dir workflow (for any non-draft PA — mirrors list_pas), have ever
    acted on it (ApprovalEventMirror — covers a completed task an open-tasks-only
    check like _can_act_on_claim would miss), or are a current approval participant
    (tasks-table check, same as get_pa_permissions/_can_act_on_claim)."""
    if pa.created_by == user_id or role == "system_admin":
        return True
    from app.api.v1.expenses import _CAN_PAY, _can_act_on_claim, _get_workflow_defs
    if role in _CAN_PAY:
        return True
    wf = await _get_workflow_defs(db)
    pa_dir_roles = {s.get("role") for s in (wf.get("pa_dir") or [])}
    if role in pa_dir_roles and pa.status != "draft":
        return True
    from sqlalchemy import select as sa_select, func as sa_func
    from app.models.approval_event_mirror import ApprovalEventMirror as AEM
    acted = (await db.execute(
        sa_select(sa_func.count()).select_from(AEM).where(
            AEM.document_id == pa.id, AEM.actor_id == user_id
        )
    )).scalar_one()
    if acted:
        return True
    return await _can_act_on_claim(db, pa, user_id, role)


@router.get("/{pa_id}", response_model=PaResponse)
async def get_pa(pa_id: uuid.UUID, db: SessionDep, user: CurrentUserDep):
    pa = await pa_crud.get_by_id(db, pa_id)
    if not pa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PA not found")
    if not await _can_view_pa(db, pa, uuid.UUID(user["sub"]), user.get("role", "")):
        raise HTTPException(status_code=403, detail="Not authorized to view this PA")
    return PaResponse.model_validate(pa)


class PaPermissions(BaseModel):
    is_owner: bool
    can_approve: bool      # may approve / return / reject the current pending step
    can_pay: bool          # may record payment (status = approved)


@router.get("/{pa_id}/permissions", response_model=PaPermissions)
async def get_pa_permissions(pa_id: uuid.UUID, db: SessionDep, user: CurrentUserDep):
    """Whether the current user may act on this PA — resolved server-side against the
    shared tasks table (same contract as the expense-claim endpoint) plus the user's
    role union (JWT base role ∪ identity user_roles additional roles), since approval
    roles (Finance BP, etc.) are assignments, not JWT role claims."""
    from app.api.v1.expenses import _PAY_ASSIGNED, _PAY_PRIMARY, _can_act_on_claim

    pa = await pa_crud.get_by_id(db, pa_id)
    if not pa:
        raise HTTPException(status_code=404, detail="PA not found")

    user_id = uuid.UUID(user["sub"])
    role = user.get("role", "")
    is_admin = role == "system_admin"
    is_owner = pa.created_by == user_id

    can_approve = False
    if pa.status in ("submitted", "in_review") and not is_owner:
        # _can_act_on_claim only reads .id — works for any document with open tasks.
        can_approve = is_admin or await _can_act_on_claim(db, pa, user_id)

    can_pay = False
    if pa.status == "approved":
        codes = await _user_role_codes(db, user_id, role)
        # Mirrors finance-api's authoritative gate (_PAY_ROLES / _PAY_ROLES_ASSIGNED)
        # — NOT _CAN_PAY, which is a visibility set and still includes ap_clerk.
        can_pay = role in _PAY_PRIMARY or bool(codes & _PAY_ASSIGNED)

    return PaPermissions(is_owner=is_owner, can_approve=can_approve, can_pay=can_pay)


@router.get("/{pa_id}/history", response_model=list[ApprovalEventOut])
async def get_pa_history(pa_id: uuid.UUID, db: SessionDep, user: CurrentUserDep):
    """Return approval events for this PA from the shared approval_events table."""
    from sqlalchemy import select
    from app.models.approval_event_mirror import ApprovalEventMirror

    pa = await pa_crud.get_by_id(db, pa_id)
    if not pa:
        raise HTTPException(status_code=404, detail="PA not found")
    if not await _can_view_pa(db, pa, uuid.UUID(user["sub"]), user.get("role", "")):
        raise HTTPException(status_code=403, detail="Not authorized to view this PA")

    result = await db.execute(
        select(ApprovalEventMirror)
        .where(ApprovalEventMirror.document_id == pa_id)
        .order_by(ApprovalEventMirror.created_at.asc())
    )
    events = result.scalars().all()
    return [
        ApprovalEventOut(
            id=e.id,
            step_idx=e.step_idx,
            action=e.action,
            actor_id=e.actor_id,
            actor_role=e.actor_role,
            comment=e.comment,
            created_at=e.created_at.isoformat(),
        )
        for e in events
    ]


@router.post("/{pa_id}/action", response_model=PaResponse)
async def pa_action(
    pa_id: uuid.UUID,
    body: PaActionRequest,
    db: SessionDep,
    user: CurrentUserDep,
    token: BearerTokenDep,
):
    pa = await pa_crud.get_by_id(db, pa_id)
    if not pa:
        raise HTTPException(status_code=404, detail="PA not found")

    # Only the creator may submit their own PA — see the matching guard in
    # expenses.py::expense_action for why this test lives in the calling
    # service rather than in approval-api's submit branch. Every other action
    # (approve / return / reject / cancel / recall) is gated by the engine.
    if body.action.lower() == "submit" and user.get("role") != "system_admin":
        if uuid.UUID(user["sub"]) != pa.created_by:
            raise HTTPException(
                status_code=403,
                detail="Only the creator can submit this payment application",
            )
    # PA-DIR uses its own configurable workflow; everything else uses "pa".
    # NOT `po_id is None` — an EPMS Purchase Agreement PA also has no PO, and
    # approving it through workflow_defs["pa_dir"] would run it down the wrong
    # chain entirely. See PaymentApplication.is_direct.
    action_key = "pa_dir" if pa.is_direct else "pa"
    # NOTE: "process" (payment) is intentionally NOT in PaActionRequest's
    # Literal — OA's only payment entry is POST /pa/{id}/pay, which forwards
    # to finance-api's unified executor (Phase 0-B1.5).
    try:
        await delegate_action(action_key, str(pa_id), body.action, body.comment, token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    await db.refresh(pa)
    return PaResponse.model_validate(pa)


@router.post("/{pa_id}/pay", response_model=PaResponse)
async def record_payment(
    pa_id: uuid.UUID,
    body: PaymentRecord,
    db: SessionDep,
    _: CurrentUserDep,
    token: BearerTokenDep,
):
    pa = await pa_crud.get_by_id(db, pa_id)
    if not pa:
        raise HTTPException(status_code=404, detail="PA not found")
    # Phase 0-B1.5: forward to finance-api's unified payment executor — it
    # owns can_pay (incl. additional-role assignments via identity user_roles),
    # the status flip, payment_records (bank reference finally persisted) and
    # the posting event.
    try:
        # credit_ids is deliberately NOT passed, so finance-api applies its
        # automatic FIFO vendor-credit default (Phase B).
        #
        # OA's confirmation dialog for this action — ProcessPaymentModal
        # (oa/src/components/ProcessPaymentModal.tsx, rendered from
        # oa/src/pages/pa/PaDetailPage.tsx) — shows the gross `payment_amount`
        # next to "Confirm Payment", so an operator here confirms a figure that
        # is not the cash actually sent. EPMS's PA detail dialog had the same
        # gap and was fixed; this one was NOT, deliberately: the OA Direct-PA
        # feature is slated for removal (product decision, 2026-08-07), so it
        # is not getting the netting preview or per-credit deselection.
        #
        # Do not "finish" this by wiring credit_ids through — without a preview
        # that would be a blind toggle over money. If the removal is ever
        # cancelled, give ProcessPaymentModal the same treatment EPMS's dialog
        # got and add the parameter together with it. Until then the netting is
        # explained on the remittance advice and in
        # GET /finance/v1/vendor-credits/{id}/applications.
        await finance_client.execute_payment(
            doc_kind="pa_dir" if pa.is_direct else "pa",
            doc_id=pa_id, bearer_token=token,
            bank_account_id=body.bank_account_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    await db.refresh(pa)
    return PaResponse.model_validate(pa)
