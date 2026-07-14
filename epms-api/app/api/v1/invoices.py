"""Invoice endpoints."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import BearerToken, CurrentUserPayload, SessionDep, require_permission, require_roles
from app.core.access_scope import build_scope
from app.crud import invoice as invoice_crud
from app.crud import vendor as vendor_crud
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User
from app.schemas.invoice import (
    InvoiceCreate,
    InvoiceExceptionRequest,
    InvoiceListResponse,
    InvoiceMatchRequest,
    InvoiceResponse,
    InvoiceUpdate,
)
from app.services.notification import dispatch_task_notification, fire_and_forget_notify
from app.services import finance_client
from app.services import finance_sync

router = APIRouter(prefix="/invoices", tags=["invoices"])

_AP_ROLES = ("system_admin", "ap_clerk", "finance_manager", "finance_bp")
ApDep = Annotated[dict, Depends(require_roles(*_AP_ROLES))]
InvoiceUploadDep = Annotated[dict, Depends(require_permission("invoice_upload"))]


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
    own_uploads = scope["user_id"] if scope["role"] == "requester" else None
    items, total = await invoice_crud.get_all(
        db, status=status, vendor_id=vendor_id, po_id=po_id, search=search,
        po_ids_subq=scope["po_subq"],
        own_uploads_user_id=own_uploads,
        page=page, page_size=page_size,
    )
    return InvoiceListResponse(items=items, total=total)


@router.post("", response_model=InvoiceResponse, status_code=201)
async def upload_invoice(body: InvoiceCreate, db: SessionDep, user: InvoiceUploadDep, token: BearerToken):
    vendor = await vendor_crud.get_by_id(db, body.vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    inv = await invoice_crud.create(
        db, body, vendor_name=vendor.name, uploaded_by=uuid.UUID(user["sub"])
    )
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
        if not await invoice_crud.is_visible(db, inv, scope):
            raise HTTPException(status_code=404, detail="Invoice not found")
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
    user: ApDep,
    token: BearerToken,
):
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status not in ("unmatched", "exception"):
        raise HTTPException(status_code=409, detail=f"Invoice already in status '{inv.status}'")
    from app.crud.invoice import AllocationImbalance, LegacyMatchUnsupported
    try:
        result = await invoice_crud.match(db, inv, body, matched_by=uuid.UUID(user["sub"]))

        # After matching, notify the PR requester to create a PA.
        # Lookup chain: invoice.po_id → PO.pr_id → PR.created_by (requester)
        await _notify_requester_create_pa(db, result)

        # Sync to finance: posted if matched, draft otherwise. Fail-open.
        await finance_sync.sync_ap_invoice(db, result, token)

        return result
    except (AllocationImbalance, LegacyMatchUnsupported) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


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
