"""Purchase Order endpoints."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from sqlalchemy import select

from app.core.authz import require_permission
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.core.access_scope import build_scope
from app.services import approval_client as approval_client
from app.services.approval_client import delegate_action
from app.crud import po as po_crud
from app.crud import vendor as vendor_crud
from app.crud import gr as gr_crud
from app.crud.current_step import enrich_current_step
from app.models.admin_audit_log import AdminAuditLog
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.schemas.po import PlaceOrderRequest, PoActionRequest, PoCreate, PoImportedDetailsUpdate, PoListResponse, PoResponse, PoUpdate
from app.schemas.pr import ApprovalEventResponse
from app.services.notification import fire_and_forget_notify

router = APIRouter(prefix="/po", tags=["purchase-orders"])

PoWriteDep = Annotated[dict, Depends(require_permission("epms.po.write"))]
PoEditImportedDep = Annotated[dict, Depends(require_permission("epms.po.edit_imported"))]


@router.get("", response_model=PoListResponse)
async def list_pos(
    db: SessionDep,
    user: CurrentUserPayload,
    status: str | None = Query(default=None),
    vendor_id: uuid.UUID | None = Query(default=None),
    pr_id: uuid.UUID | None = Query(default=None),
    pr_type: int | None = Query(default=None),
    department_id: uuid.UUID | None = Query(default=None),
    is_prepaid: bool | None = Query(default=None),
    mine: bool = False,
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, le=200),
):
    scope = await build_scope(db, user)
    # Access Control Matrix gate — return empty when view_po is disabled.
    if not scope["perms"].get("view_po", False):
        return {"items": [], "total": 0}
    po_subq = scope["po_subq"]
    created_by = uuid.UUID(user["sub"]) if mine else None

    items, total = await po_crud.get_all(
        db, status=status, vendor_id=vendor_id, pr_id=pr_id,
        pr_type=pr_type, department_id=department_id, is_prepaid=is_prepaid,
        created_by=created_by, search=search,
        po_ids_subq=po_subq,
        page=page, page_size=page_size,
    )
    await enrich_current_step(db, "po", items)
    unpaid_invoice_po_ids: set[uuid.UUID] = set()
    # Map each listed PO's linked PR to its requester (PR.created_by) so the
    # frontend can scope actions like PA creation to the requester's own POs.
    # Resolved here in batch because pr_requester_id is not a column on the PO.
    pr_requester_map: dict[uuid.UUID, uuid.UUID] = {}
    if items:
        listed_ids = [po.id for po in items]
        rows = await db.execute(
            select(Invoice.po_id).where(
                Invoice.po_id.in_(listed_ids),
                Invoice.status != "paid",
            ).distinct()
        )
        unpaid_invoice_po_ids = {r for r in rows.scalars().all() if r is not None}
        # An invoice can pay for several POs: the header links one, the rest hang
        # off invoice_po_allocations. Counting only the header hid the allocated
        # POs from the PA create page's PO picker (it offers
        # `is_prepaid || has_unpaid_invoice`), so nobody could raise their PA.
        alloc_rows = await db.execute(
            select(InvoicePoAllocation.po_id)
            .join(Invoice, Invoice.id == InvoicePoAllocation.invoice_id)
            .where(
                InvoicePoAllocation.po_id.in_(listed_ids),
                Invoice.status != "paid",
            ).distinct()
        )
        unpaid_invoice_po_ids |= {r for r in alloc_rows.scalars().all() if r is not None}

        pr_ids = [po.pr_id for po in items if po.pr_id is not None]
        if pr_ids:
            pr_rows = await db.execute(
                select(PurchaseRequest.id, PurchaseRequest.created_by).where(
                    PurchaseRequest.id.in_(pr_ids)
                )
            )
            pr_requester_map = {pid: cby for pid, cby in pr_rows.all()}
    po_responses = []
    for po in items:
        data = PoResponse.model_validate(po).model_dump()
        data["has_unpaid_invoice"] = po.id in unpaid_invoice_po_ids
        data["pr_requester_id"] = pr_requester_map.get(po.pr_id) if po.pr_id else None
        po_responses.append(data)
    return {"items": po_responses, "total": total}


@router.post("", response_model=PoResponse, status_code=status.HTTP_201_CREATED)
async def create_po(body: PoCreate, db: SessionDep, user: PoWriteDep):
    vendor = await vendor_crud.get_by_id(db, body.vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return await po_crud.create(
        db, body,
        vendor_code=vendor.code,
        vendor_name=vendor.name,
        created_by=uuid.UUID(user["sub"]),
    )


@router.get("/{po_id}", response_model=PoResponse)
async def get_po(po_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    from app.core.access_scope import is_po_visible
    po = await po_crud.get_by_id(db, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="PO not found")
    scope = await build_scope(db, user)
    if not await is_po_visible(db, po_id, scope):
        raise HTTPException(status_code=404, detail="PO not found")
    # Resolve the linked PR's requester so the frontend can gate GR creation on the
    # actual requester identity (not the PO creator / not a generic role).
    data = PoResponse.model_validate(po).model_dump()
    data["pr_requester_id"] = await gr_crud.get_pr_requester_id(db, po.pr_id)
    data["has_invoice"] = await po_crud.has_any_invoice(db, po_id)
    return data


@router.patch("/{po_id}", response_model=PoResponse)
async def update_po(po_id: uuid.UUID, body: PoUpdate, db: SessionDep, user: PoWriteDep):
    po = await po_crud.get_by_id(db, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="PO not found")
    if po.status not in ("draft", "returned"):
        raise HTTPException(status_code=409, detail=f"Cannot edit PO in status '{po.status}'")

    vendor_code = vendor_name = None
    if body.vendor_id is not None:
        vendor = await vendor_crud.get_by_id(db, body.vendor_id)
        if vendor is None:
            raise HTTPException(status_code=404, detail="Vendor not found")
        vendor_code = vendor.code
        vendor_name = vendor.name

    return await po_crud.update(db, po, body, vendor_code=vendor_code, vendor_name=vendor_name)


@router.patch("/{po_id}/imported-details", response_model=PoResponse)
async def update_imported_details(
    po_id: uuid.UUID, body: PoImportedDetailsUpdate, db: SessionDep, user: PoEditImportedDep,
):
    """Fill in buyer-supplied detail on an NC-imported PO.

    Deliberately separate from PATCH /po/{po_id}. That endpoint accepts a new
    vendor_id, a new currency and a full replacement line_items list (crud.update
    deletes and rebuilds the lines), so relaxing its draft/returned status gate
    for NC POs would hand anyone holding epms.po.write the ability to rewrite the
    money on an order already in the invoice/payment flow. Here the request model
    itself makes those fields unreachable.
    """
    po = await po_crud.get_by_id(db, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="PO not found")
    if po.source != "nc":
        raise HTTPException(
            status_code=409, detail="Only NC-imported POs can be edited here")
    # No status gate. Everything this endpoint writes is detail the ERP has no
    # column for, and the sync will not overwrite it (writer.upsert drops those
    # columns once buyer_edited_at is set), so a later NC change cannot collide
    # with it — and a closed or in-approval order gets asked about its Incoterms
    # just as often as an open one.
    #
    # tax_rate is the exception, and is guarded on evidence rather than status:
    # changing it re-derives tax_amount and total off the same subtotal, which
    # on a PO an invoice already points at moves the very figure the 3-way
    # variance was measured against. Guarded on the VALUE changing, not on the
    # key being present — the edit form re-sends the current rate on every CAD
    # save, so rejecting on presence would make an invoiced CAD order unsavable.
    _fields = body.model_fields_set
    rate_moves = ("tax_rate" in _fields and body.tax_rate is not None
                  and body.tax_rate != po.tax_rate)
    # Buyer-added lines carry money too — adding, editing or removing one moves
    # the same subtotal the rate does, and also changes which lines an invoice
    # could be allocated across. Same evidence, same refusal. Compared by
    # CONTENT for the same reason as the rate: the form re-sends the whole
    # manual set on every save.
    lines_move = ("manual_lines" in _fields and body.manual_lines is not None
                  and po_crud.manual_lines_changed(po, body.manual_lines))
    if (rate_moves or lines_move) and await po_crud.has_any_invoice(db, po_id):
        what = "Tax rate" if rate_moves else "Added line items"
        raise HTTPException(
            status_code=409,
            detail=f"{what} cannot be changed once an invoice has been raised "
                   "against this PO. Every other field is still editable.",
        )

    try:
        po, before, after = await po_crud.update_imported_details(db, po, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if after:
        db.add(AdminAuditLog(
            actor_id=uuid.UUID(user["sub"]),
            actor_email=user.get("email", ""),
            action="edit",
            system="epms",
            entity="po",
            record_id=po.id,
            record_number=po.number,
            before=before,
            after=after,
        ))
        await db.flush()
    return po


async def _generate_po_pdf_background(po_id: uuid.UUID, po_number: str, token: str) -> None:
    """Generate and attach the approved-PO PDF using a fresh DB session (PRD §3.4)."""
    import asyncio
    import logging
    from app.services.pdf_po import generate_po_pdf
    from app.models.po import PurchaseOrder
    from app.models.po_attachment import PoAttachment
    from app.models.config import CompanyConfig
    from app.models.user import User
    from app.core.access_scope import role_holder_ids
    from app.services.attachment_helper import upload_to_file_server
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import select as sa_select

    log = logging.getLogger(__name__)
    try:
        async with AsyncSessionLocal() as fresh_db:
            po_row = (await fresh_db.execute(
                sa_select(PurchaseOrder).where(PurchaseOrder.id == po_id)
            )).scalar_one_or_none()
            if po_row is None:
                return
            cfg = (await fresh_db.execute(sa_select(CompanyConfig).limit(1))).scalar_one_or_none()
            company_name = cfg.name if cfg else "EPMS"

            existing = (await fresh_db.execute(
                sa_select(PoAttachment).where(
                    PoAttachment.po_id == po_id,
                    PoAttachment.filename == f"{po_number}.pdf",
                )
            )).scalar_one_or_none()
            if existing:
                return

            # Type 1 POs carry a signature block naming the OPM as our
            # company's signatory. role_holder_ids counts a PRIMARY (users.role)
            # or ADDITIONAL (identity's user_roles) "opm" role, active users
            # only. Leave the name blank unless exactly one holder is found —
            # never print an arbitrarily-chosen name on a vendor-facing document.
            signatory_name = None
            if po_row.type == 1:
                opm_holders = (await role_holder_ids(fresh_db, codes=("opm",))).get("opm", set())
                if len(opm_holders) == 1:
                    signatory_name = (await fresh_db.execute(
                        sa_select(User.full_name).where(User.id == next(iter(opm_holders)))
                    )).scalar_one_or_none()

            loop = asyncio.get_event_loop()
            pdf_bytes = await loop.run_in_executor(
                None, generate_po_pdf, po_row, company_name,
                cfg.pdf_templates if cfg else None,
                cfg.logo_data_url if cfg else None,
                signatory_name,
            )
            storage_key = await upload_to_file_server(
                pdf_bytes, f"{po_number}.pdf", "application/pdf", "po", po_id, token,
            )
            fresh_db.add(PoAttachment(
                po_id=po_id, filename=f"{po_number}.pdf",
                content_type="application/pdf", file_size=len(pdf_bytes),
                storage_key=storage_key,
            ))
            await fresh_db.commit()
            log.info("PO PDF uploaded to file server for %s (key=%s)", po_number, storage_key)
    except Exception:
        log.warning("PO PDF generation failed for %s", po_id, exc_info=True)


@router.post("/{po_id}/action", response_model=PoResponse)
async def po_action(
    po_id: uuid.UUID,
    body: PoActionRequest,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    po = await po_crud.get_by_id(db, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="PO not found")
    try:
        await delegate_action("po", str(po_id), body.action, body.comment, token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    await db.refresh(po)
    if po.status == "approved":
        import asyncio
        asyncio.create_task(_generate_po_pdf_background(po_id, po.number, token))

    new_tasks_result = await db.execute(
        select(Task).where(
            Task.document_type == "po",
            Task.document_id == po_id,
            Task.is_completed.is_(False),
        )
    )
    for task in new_tasks_result.scalars().all():
        fire_and_forget_notify(task, db)
    return po


@router.post("/{po_id}/place-order", response_model=PoResponse)
async def place_order(
    po_id: uuid.UUID,
    body: PlaceOrderRequest,
    db: SessionDep,
    user: PoWriteDep,
    token: BearerToken,
):
    po = await po_crud.get_by_id(db, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="PO not found")

    # Fetch vendor contact email for the audit trail / email send
    vendor = await vendor_crud.get_by_id(db, po.vendor_id)
    vendor_email = vendor.contact_email if vendor else None

    if body.method == "email":
        if not body.to and not vendor_email:
            raise HTTPException(
                status_code=422,
                detail="Vendor has no contact email. Add one in Vendor Master before using email placement.",
            )
        # Send the email using the body provided by the frontend (already rendered from template)
        if body.to and body.subject and body.body:
            from app.crud import config as config_crud
            from app.models.po_attachment import PoAttachment
            from app.services.attachment_helper import proxy_download
            from app.services.email import send_email
            import logging as _logging
            cfg = await config_crud.get_or_create(db)
            # PO-to-vendor SMTP profile (po_smtp_*) with per-field fallback to
            # the internal task-notification profile (smtp_*). Allows an admin
            # to override only the From address while still relaying through
            # the same server, if desired.
            smtp = {
                "smtp_host":     cfg.po_smtp_host     if cfg.po_smtp_host     is not None else cfg.smtp_host,
                "smtp_port":     cfg.po_smtp_port     if cfg.po_smtp_port     is not None else cfg.smtp_port,
                "smtp_user":     cfg.po_smtp_user     if cfg.po_smtp_user     is not None else cfg.smtp_user,
                "smtp_password": cfg.po_smtp_password if cfg.po_smtp_password is not None else cfg.smtp_password,
                "smtp_use_tls":  cfg.po_smtp_use_tls  if cfg.po_smtp_use_tls  is not None else cfg.smtp_use_tls,
                "smtp_from":     cfg.po_smtp_from     if cfg.po_smtp_from     is not None else cfg.smtp_from,
            }
            # Fetch the PO PDF attachment and include it in the email
            attachments: list[tuple[str, bytes, str]] = []
            pdf_filename = f"{po.number}.pdf"
            pdf_row = (await db.execute(
                select(PoAttachment).where(
                    PoAttachment.po_id == po_id,
                    PoAttachment.filename == pdf_filename,
                )
            )).scalar_one_or_none()
            if pdf_row:
                try:
                    if pdf_row.storage_key:
                        import httpx
                        from app.core.config import settings as _settings
                        async with httpx.AsyncClient(timeout=20.0) as _client:
                            _resp = await _client.get(
                                f"{_settings.FILE_SERVER_URL}/files/{pdf_row.storage_key}",
                                headers={"Authorization": f"Bearer {token}"},
                            )
                        if _resp.is_success:
                            attachments.append((pdf_filename, _resp.content, "application/pdf"))
                    elif pdf_row.file_data:
                        attachments.append((pdf_filename, pdf_row.file_data, "application/pdf"))
                except Exception as _exc:
                    _logging.getLogger(__name__).warning(
                        "Could not fetch PO PDF for email attachment (%s): %s", po.number, _exc
                    )
            try:
                html_body = body.body.replace("\n", "<br>")
                await send_email(body.to, body.subject, html_body, cc=body.cc or None, **smtp, attachments=attachments)
            except Exception as exc:
                _logging.getLogger(__name__).warning("PO email send failed for %s: %s", po.number, exc)
                # Non-blocking: still mark as issued even if email fails (logged)

    try:
        return await po_crud.place_order(
            db, po, body,
            actor_id=uuid.UUID(user["sub"]),
            actor_role=user.get("role", ""),
            vendor_email=vendor_email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/{po_id}/events", response_model=list[ApprovalEventResponse])
async def po_approval_events(po_id: uuid.UUID, db: SessionDep, _: CurrentUserPayload):
    po = await po_crud.get_by_id(db, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="PO not found")
    return await po_crud.get_approval_events(db, po_id)


@router.get("/{po_id}/workflow-steps")
async def po_workflow_steps(po_id: uuid.UUID, user: CurrentUserPayload, token: BearerToken):
    try:
        return await approval_client.get_workflow_steps("po", str(po_id), token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
