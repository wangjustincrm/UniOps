"""Payment Application endpoints."""
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from sqlalchemy import select

from app.core.deps import BearerToken, CurrentUserPayload, SessionDep, require_roles
from app.core.access_scope import build_scope
from app.services.approval_client import delegate_action
from app.services import finance_client
from app.crud import pa as pa_crud
from app.crud import po as po_crud
from app.models.config import CompanyConfig
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.pa import PaymentApplication
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.config import PrepaymentConfig
from app.schemas.pa import PaActionRequest, PaCreate, PaListResponse, PaResponse, PaUpdate
from app.schemas.pr import ApprovalEventResponse

router = APIRouter(prefix="/pa", tags=["payment-applications"])

_PA_WRITE_ROLES = ("system_admin", "finance_bp", "finance_manager", "ap_clerk", "requester")
PaWriteDep = Annotated[dict, Depends(require_roles(*_PA_WRITE_ROLES))]


@router.get("", response_model=PaListResponse)
async def list_pas(
    db: SessionDep,
    user: CurrentUserPayload,
    status: str | None = Query(default=None),
    po_id: uuid.UUID | None = Query(default=None),
    vendor_id: uuid.UUID | None = Query(default=None),
    mine: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, le=200),
):
    scope = await build_scope(db, user)
    # Access Control Matrix gate — return empty when view_pa is disabled.
    if not scope["perms"].get("view_pa", False):
        return {"items": [], "total": 0}
    created_by = scope["user_id"] if scope["restrict"] and scope["role"] == "requester" else (
        uuid.UUID(user["sub"]) if mine else None
    )
    items, total = await pa_crud.get_all(
        db, status=status, po_id=po_id, vendor_id=vendor_id,
        created_by=created_by,
        po_ids_subq=scope["po_subq"],
        page=page, page_size=page_size,
    )
    return {"items": [PaResponse.model_validate(pa) for pa in items], "total": total}


async def _get_prepayment_config(db: SessionDep) -> PrepaymentConfig:
    cfg_row = await db.execute(select(CompanyConfig).limit(1))
    cfg = cfg_row.scalar_one_or_none()
    raw = cfg.prepayment_config if cfg and cfg.prepayment_config else {}
    return PrepaymentConfig.model_validate(raw)


@router.post("", response_model=PaResponse, status_code=201)
async def create_pa(body: PaCreate, db: SessionDep, user: PaWriteDep, token: BearerToken):
    po = await po_crud.get_by_id(db, body.po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # A plain requester may only pay against POs linked to a PR they raised. The
    # PA write role list includes 'requester', and a requester carrying a special
    # role assignment (e.g. finance_bp) would otherwise have unrestricted PO scope,
    # so enforce ownership here on the JWT base role regardless of that widening.
    if user.get("role") == "requester":
        pr_requester_id = None
        if po.pr_id is not None:
            pr_requester_id = (await db.execute(
                select(PurchaseRequest.created_by).where(PurchaseRequest.id == po.pr_id)
            )).scalar_one_or_none()
        if pr_requester_id != uuid.UUID(user["sub"]):
            raise HTTPException(
                status_code=403,
                detail="You can only create payments for purchase orders linked to your own requisitions.",
            )

    # ── Prepayment-specific guards ─────────────────────────────────────────────
    if body.pa_type == "prepayment":
        vendor = await db.get(Vendor, po.vendor_id)
        pp_cfg = await _get_prepayment_config(db)

        # Effective cap: vendor override takes priority over system-wide cap
        effective_cap = (
            float(vendor.max_prepayment_pct)
            if vendor and vendor.max_prepayment_pct is not None
            else pp_cfg.max_prepayment_pct
        )

        # PP-001: vendor must allow prepayment (effective_cap > 0)
        if not effective_cap or effective_cap <= 0:
            raise HTTPException(
                status_code=422,
                detail="This vendor does not allow prepayment. Set Max Prepayment % on the vendor record.",
            )

        # PP-002/003: requested prepayment % must not exceed cap
        if body.prepayment_pct is None:
            raise HTTPException(status_code=422, detail="prepayment_pct is required for prepayment PAs.")
        if float(body.prepayment_pct) > effective_cap:
            raise HTTPException(
                status_code=422,
                detail=f"Prepayment % ({body.prepayment_pct}%) exceeds the allowed cap of {effective_cap}% for this vendor.",
            )
        # PP-003: also enforce system-wide cap independently
        if float(body.prepayment_pct) > pp_cfg.max_prepayment_pct:
            raise HTTPException(
                status_code=422,
                detail=f"Prepayment % ({body.prepayment_pct}%) exceeds the system-wide cap of {pp_cfg.max_prepayment_pct}%.",
            )

        # PP-004: only one open prepayment PA per PO
        existing = (await db.execute(
            select(PaymentApplication.id).where(
                PaymentApplication.po_id == body.po_id,
                PaymentApplication.pa_type == "prepayment",
                PaymentApplication.status.notin_(["cancelled", "rejected"]),
            )
        )).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=409,
                detail="A prepayment PA already exists for this PO. Settle or cancel it before creating a new one.",
            )

    elif body.pa_type in ("settlement", "balance"):
        # PP-009: settlement/balance must reference the original prepayment PA
        if not body.prepayment_pa_id:
            raise HTTPException(
                status_code=422,
                detail=f"prepayment_pa_id is required for {body.pa_type} PAs.",
            )
        orig = (await db.execute(
            select(PaymentApplication).where(
                PaymentApplication.id == body.prepayment_pa_id,
                PaymentApplication.po_id == body.po_id,
                PaymentApplication.pa_type == "prepayment",
            )
        )).scalar_one_or_none()
        if orig is None:
            raise HTTPException(
                status_code=422,
                detail="Referenced prepayment PA not found or does not belong to this PO.",
            )

        # Net payable = full charge − prepayment applied. Must be ≥ 0:
        # an overpaid prepayment is a credit-note situation, not a negative payment.
        from decimal import Decimal as _D
        applied = body.prepayment_applied or _D("0")
        net = (body.subtotal + body.tax_amount + body.shipping_amount
               + body.other_charges - applied)
        if net < _D("0"):
            raise HTTPException(
                status_code=422,
                detail="Net payable is negative — prepayment exceeds the final amount. "
                       "A credit note is required; settle with prepayment_applied ≤ total.",
            )

    else:
        # PP-008: for regular PAs, block if any open prepayment PA exists on this PO
        blocking = (await db.execute(
            select(PaymentApplication.pa_number).where(
                PaymentApplication.po_id == body.po_id,
                PaymentApplication.pa_type == "prepayment",
                PaymentApplication.status.notin_(["cancelled", "rejected", "processed"]),
            )
        )).scalar_one_or_none()
        if blocking:
            raise HTTPException(
                status_code=409,
                detail=f"Prepayment PA {blocking} is still open. Settle or cancel it before creating a regular PA.",
            )

    # ── Invoice validation ─────────────────────────────────────────────────────
    if body.invoice_ids:
        result = await db.execute(
            select(Invoice.id, Invoice.po_id).where(Invoice.id.in_(body.invoice_ids))
        )
        rows = result.all()
        found_ids = {r.id for r in rows}
        not_found = len(set(body.invoice_ids)) - len(found_ids)
        if not_found:
            raise HTTPException(status_code=422, detail=f"{not_found} invoice(s) not found")

        # An invoice "belongs to" this PO if it is header-linked (Invoice.po_id)
        # OR allocated to it via the multi-PO allocation table. The invoice list
        # the user picks from uses the same OR rule (crud.invoice.get_all), so the
        # header-only check below would wrongly 422 any invoice whose primary PO
        # differs from this one but is allocated here.
        direct_ids = {r.id for r in rows if r.po_id == body.po_id}
        need_alloc_check = found_ids - direct_ids
        if need_alloc_check:
            alloc_rows = await db.execute(
                select(InvoicePoAllocation.invoice_id).where(
                    InvoicePoAllocation.invoice_id.in_(need_alloc_check),
                    InvoicePoAllocation.po_id == body.po_id,
                ).distinct()
            )
            need_alloc_check -= set(alloc_rows.scalars().all())
        if need_alloc_check:
            raise HTTPException(
                status_code=422,
                detail=f"Invoice(s) do not belong to PO {po.number}: "
                       f"{', '.join(str(i) for i in need_alloc_check)}",
            )

    created = await pa_crud.create(
        db, body,
        po_number=po.number,
        vendor_id=po.vendor_id,
        vendor_name=po.vendor_name,
        created_by=uuid.UUID(user["sub"]),
    )

    # Settlement is net-aware. Net payable (created.payment_amount) is the cash to
    # move = max(final − prepaid, 0):
    #   • net  > 0 → real balance payment: leave as draft; the normal submit →
    #                approval → process(bank) flow pays it (marks prepayment settled
    #                on approval).
    #   • net == 0 → pure reconciliation, NO cash / NO bank:
    #       – variance == 0 → auto-reconcile now (no approval, no task)
    #       – variance != 0 (overpaid) → finance confirmation task gates it
    if created.pa_type == "settlement" and created.payment_amount == 0:
        actor = uuid.UUID(user["sub"])
        prepaid = orig.payment_amount if orig is not None else Decimal("0")
        variance = pa_crud._settlement_variance(created, prepaid)
        if variance == 0:
            # exact match → reconcile now, no approval, no task, no cash
            await pa_crud.finalize_settlement_reconciliation(db, created, actor)
            inv_ids = [uuid.UUID(i) for i in created.invoice_ids]
            await finance_client.mark_ap_settled(invoice_ids=inv_ids, bearer_token=token)
        else:
            # overpaid → finance must confirm the credit note before reconciling
            await pa_crud.create_settlement_confirm_task(db, created, variance)
            created.status = "submitted"
            await db.flush()
            await db.refresh(created)

    return created


@router.get("/{pa_id}", response_model=PaResponse)
async def get_pa(pa_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    from app.core.access_scope import is_pa_visible
    pa = await pa_crud.get_by_id(db, pa_id)
    if pa is None or pa.po_id is None:  # Direct PAs (NULL po_id) belong to OA, not EPMS
        raise HTTPException(status_code=404, detail="PA not found")
    scope = await build_scope(db, user)
    if not await is_pa_visible(db, pa, scope):
        raise HTTPException(status_code=404, detail="PA not found")
    return pa


@router.patch("/{pa_id}", response_model=PaResponse)
async def update_pa(pa_id: uuid.UUID, body: PaUpdate, db: SessionDep, user: PaWriteDep):
    pa = await pa_crud.get_by_id(db, pa_id)
    if pa is None or pa.po_id is None:  # Direct PAs (NULL po_id) belong to OA, not EPMS
        raise HTTPException(status_code=404, detail="PA not found")
    if pa.status not in ("draft", "returned"):
        raise HTTPException(status_code=409, detail=f"Cannot edit PA in status '{pa.status}'")
    return await pa_crud.update(db, pa, body)


@router.post("/{pa_id}/action", response_model=PaResponse)
async def pa_action(
    pa_id: uuid.UUID,
    body: PaActionRequest,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    pa = await pa_crud.get_by_id(db, pa_id)
    if pa is None or pa.po_id is None:  # Direct PAs (NULL po_id) belong to OA, not EPMS
        raise HTTPException(status_code=404, detail="PA not found")
    try:
        if body.action == "process":
            # Payment is not a workflow action (Phase 0-B1.5): forward to
            # finance-api's unified executor — it owns can_pay, the status
            # flip, payment_records, invoice marking and the posting event.
            result = await finance_client.execute_payment(
                doc_kind="pa", doc_id=pa_id, bearer_token=token,
                notes=body.comment, bank_account_id=body.bank_account_id,
            )
        else:
            result = await delegate_action("pa", str(pa_id), body.action, body.comment, token)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    await db.refresh(pa)
    # Generate PDF when PA transitions to approved
    if result.get("new_status") == "approved":
        # 统一 settlement：settlement PA 一旦 approved，回填来源预付为 settled
        if pa.pa_type == "settlement":
            await pa_crud.mark_prepayment_settled(
                db, pa, actor_id=uuid.UUID(user["sub"])
            )
        from app.services.pdf_pa import generate_pa_pdf
        from app.models.pa_attachment import PaAttachment
        from app.models.config import CompanyConfig
        from app.services.attachment_helper import upload_to_file_server
        from sqlalchemy import select as sa_select
        import asyncio
        cfg_r = await db.execute(sa_select(CompanyConfig).limit(1))
        cfg = cfg_r.scalar_one_or_none()
        company_name = cfg.name if cfg else "EPMS"
        loop = asyncio.get_event_loop()
        pdf_bytes = await loop.run_in_executor(
            None, generate_pa_pdf, pa, company_name,
            cfg.pdf_templates if cfg else None,
            cfg.logo_data_url if cfg else None,
        )
        existing = await db.execute(
            sa_select(PaAttachment).where(
                PaAttachment.pa_id == pa_id,
                PaAttachment.filename == f"{pa.pa_number}.pdf",
            )
        )
        if not existing.scalar_one_or_none():
            filename = f"{pa.pa_number}.pdf"
            storage_key = await upload_to_file_server(
                pdf_bytes, filename, "application/pdf", "pa", pa_id, token,
            )
            db.add(PaAttachment(
                pa_id=pa_id, filename=filename,
                content_type="application/pdf",
                file_size=len(pdf_bytes), storage_key=storage_key,
            ))
            await db.flush()

    return pa


@router.post("/{pa_id}/confirm-settlement", response_model=PaResponse)
async def confirm_settlement(
    pa_id: uuid.UUID,
    db: SessionDep,
    user: PaWriteDep,
    token: BearerToken,
):
    """Finance sign-off for a zero-cash settlement that has a variance (overpaid).
    No payment is made — it reconciles the prepayment and closes the invoice."""
    pa = await pa_crud.get_by_id(db, pa_id)
    if pa is None or pa.po_id is None:  # Direct PAs (NULL po_id) belong to OA, not EPMS
        raise HTTPException(status_code=404, detail="PA not found")
    if pa.pa_type != "settlement" or pa.payment_amount != 0:
        raise HTTPException(
            status_code=409,
            detail="Only zero-cash (net-0) settlement PAs are confirmed here.",
        )
    if pa.status == "processed":
        return pa  # idempotent
    await pa_crud.finalize_settlement_reconciliation(db, pa, uuid.UUID(user["sub"]))
    inv_ids = [uuid.UUID(i) for i in pa.invoice_ids]
    await finance_client.mark_ap_settled(invoice_ids=inv_ids, bearer_token=token)
    return pa


@router.get("/{pa_id}/events", response_model=list[ApprovalEventResponse])
async def pa_approval_events(pa_id: uuid.UUID, db: SessionDep, _: CurrentUserPayload):
    pa = await pa_crud.get_by_id(db, pa_id)
    if pa is None or pa.po_id is None:  # Direct PAs (NULL po_id) belong to OA, not EPMS
        raise HTTPException(status_code=404, detail="PA not found")
    return await pa_crud.get_approval_events(db, pa_id)
