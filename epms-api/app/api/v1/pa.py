"""Payment Application endpoints."""
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from sqlalchemy import select

from app.core.authz import require_permission
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.core.access_scope import build_scope, _effective_role_codes
from app.services import approval_client as approval_client
from app.services.approval_client import delegate_action
from app.services import finance_client
from app.crud import agreement as agr_crud
from app.crud import pa as pa_crud
from app.crud import po as po_crud
from app.crud.current_step import enrich_current_step
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.config import CompanyConfig
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.config import PrepaymentConfig
from app.schemas.pa import PaActionRequest, PaCreate, PaListResponse, PaResponse, PaUpdate
from app.schemas.pr import ApprovalEventResponse

router = APIRouter(prefix="/pa", tags=["payment-applications"])

PaWriteDep = Annotated[dict, Depends(require_permission("epms.pa.write"))]

# Invoice statuses that may back an agreement PA. Invoice statuses are
# unmatched | matched | match_review | exception | approved | paid
# (app/models/invoice.py). "match_review" is deliberately EXCLUDED: the
# agreement route's review gate would otherwise be bypassable by raising the PA
# before the reviewer answers. "paid" is excluded because it is already settled.
_PAYABLE_INVOICE_STATUSES = ("matched", "approved")


async def _validate_agreement_pa_invoices(
    db: SessionDep, agr, invoice_ids: list[uuid.UUID],
) -> None:
    """The invoice-side gate for an agreement-backed PA: every listed invoice
    must be matched to THIS agreement, cleared for payment, and — for a
    recurring agreement — linked to a CONFIRMED billing period.

    Extracted from create_pa (whole-branch review finding, Phase 1B): PATCH
    /pa/{id} reassigns invoice_ids without ever re-running this. Create a PA
    with a confirmed invoice, then PATCH the invoice list to an unconfirmed
    one (or one with schedule_id NULL, or one matched to a different
    agreement) and every gate below was silently bypassed — update_pa now
    calls this too whenever pa.agreement_id is set and body.invoice_ids is
    given.
    """
    if not invoice_ids:
        raise HTTPException(
            status_code=422,
            detail="At least one invoice matched to this agreement is required "
                   "— there is no goods-receipt override on this route.",
        )
    rows = (await db.execute(
        select(Invoice.id, Invoice.internal_ref, Invoice.agreement_id, Invoice.status,
               Invoice.schedule_id, Invoice.slip_ids, Invoice.legacy_settlement)
        .where(Invoice.id.in_(invoice_ids))
    )).all()
    if len(rows) != len(set(invoice_ids)):
        raise HTTPException(status_code=422, detail="One or more invoices not found")
    stray = [str(r.id) for r in rows if r.agreement_id != agr.id]
    if stray:
        raise HTTPException(
            status_code=422,
            detail=f"Invoice(s) not matched to agreement {agr.number}: {', '.join(stray)}")
    # …and each must have CLEARED matching. The link alone is not enough: an
    # invoice matched by a delegate sits at "match_review" with its
    # agreement_id already set, so without this check the AP review gate
    # (commit ebeb3f5) is bypassable simply by raising/editing the PA before
    # the review is answered. On the PO route the GR is independent evidence;
    # here the invoice IS the only evidence, so its review state is the
    # control. "exception"/"unmatched" are excluded for the same reason.
    not_ready = [
        f"{r.internal_ref} ({r.status})"
        for r in rows if r.status not in _PAYABLE_INVOICE_STATUSES
    ]
    if not_ready:
        raise HTTPException(
            status_code=422,
            detail="Invoice(s) not cleared for payment — a matched, reviewed "
                   f"invoice is required: {', '.join(not_ready)}")
    # recurring 免 GR,履约确认是它唯一的代偿 —— 未确认的期次不许付款。
    # milestone 本期没有验收闸门(设计 §5.3),house_account 走 slip 路径(见下方
    # 紧邻的闸门),所以这里按 agreement_type 分支,不能一刀切。
    if agr.agreement_type == "recurring":
        # Code review finding (Task 7 fix round): an INNER JOIN on
        # Invoice.schedule_id == AgreementPaymentSchedule.id silently drops
        # any invoice with schedule_id IS NULL from the result set instead of
        # flagging it — and that state is reachable, not theoretical:
        # claim_next_period returns None when nothing is claimable (out of
        # tolerance, or the schedule is exhausted), routing the invoice to
        # match_review with schedule_id left NULL; an AP reviewer's plain
        # approve() there sets status="matched" unconditionally without ever
        # touching schedule_id. That invoice would then sail through every
        # check above (payable status, "matched to this agreement") with the
        # confirmation control never having applied to it at all. So the
        # unlinked case must be rejected on its own, checked directly against
        # `rows` rather than through a join that can only see invoices
        # already linked.
        unlinked = [r.internal_ref for r in rows if r.schedule_id is None]
        if unlinked:
            raise HTTPException(
                status_code=422,
                detail=(f"Invoice(s) not linked to a billing period: {', '.join(unlinked)}. "
                        "They were never claimed against a scheduled period (out of "
                        "tolerance or no candidate row when matched) — this needs to be "
                        "resolved before a payment can be raised against them."))
        schedule_ids = [r.schedule_id for r in rows]
        unconfirmed = (await db.execute(
            select(AgreementPaymentSchedule.period_label)
            .where(AgreementPaymentSchedule.id.in_(schedule_ids),
                   AgreementPaymentSchedule.accepted_at.is_(None))
        )).scalars().all()
        if unconfirmed:
            raise HTTPException(
                status_code=422,
                detail=(f"Service has not been confirmed for {', '.join(unconfirmed)}. "
                        "The department must confirm delivery before payment can be raised."))
    # house_account 免收货,凭证就是小票。1A 时没有小票可挂,所以每张发票都被
    # 标成 legacy —— 那些存量数据必须继续放行,否则本期改动会卡死历史。
    # pa.py:94 那句 "house_account 走 slip 路径" 的注释从此才是真的。
    if agr.agreement_type == "house_account":
        unsupported = [
            r.internal_ref for r in rows
            if not (r.slip_ids or r.legacy_settlement)
        ]
        if unsupported:
            raise HTTPException(
                status_code=422,
                detail=(f"No pickup slips are attached to {', '.join(unsupported)}. "
                        "Match the invoice to the slips it covers, or settle it "
                        "explicitly without receipt evidence, before raising payment."))


@router.get("", response_model=PaListResponse)
async def list_pas(
    db: SessionDep,
    user: CurrentUserPayload,
    status: str | None = Query(default=None),
    po_id: uuid.UUID | None = Query(default=None),
    vendor_id: uuid.UUID | None = Query(default=None),
    department_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
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
        department_id=department_id, search=search,
        created_by=created_by,
        po_ids_subq=scope["po_subq"],
        agr_ids_subq=scope["agr_subq"],
        page=page, page_size=page_size,
    )
    await enrich_current_step(db, "pa", items)
    return {"items": [PaResponse.model_validate(pa) for pa in items], "total": total}


async def _get_prepayment_config(db: SessionDep) -> PrepaymentConfig:
    cfg_row = await db.execute(select(CompanyConfig).limit(1))
    cfg = cfg_row.scalar_one_or_none()
    raw = cfg.prepayment_config if cfg and cfg.prepayment_config else {}
    return PrepaymentConfig.model_validate(raw)


def _may_create_pa_on_behalf(roles: set[str], po: PurchaseOrder) -> bool:
    """Whether these role codes let the caller raise a PA against `po` that is
    not linked to their own requisition.

    This only bypasses the requester-ownership rule — the epms.pa.write matrix
    gate still applies to every caller.
    """
    # Procurement Officer pays on anyone's behalf, on any PO. Approval routing is
    # unaffected: approval-api resolves a PA's approvers from the linked PR's
    # requester/department, never from PA.created_by.
    if "procurement_officer" in roles:
        return True
    # erp_pa_officer covers only PR-less NC-imported POs — those have no
    # requisitioner for ownership to apply to in the first place.
    return "erp_pa_officer" in roles and po.pr_id is None and po.source == "nc"


@router.post("", response_model=PaResponse, status_code=201)
async def create_pa(body: PaCreate, db: SessionDep, user: PaWriteDep, token: BearerToken):
    # ── Agreement route: no PO, no GR, no receipt gate ─────────────────────────
    if body.agreement_id is not None:
        agr = await agr_crud.get_by_id(db, body.agreement_id)
        if agr is None:
            raise HTTPException(status_code=404, detail="Agreement not found")
        # No PO on this route means none of the PO-scoped prepayment/settlement/
        # balance guards below (vendor cap, ownership-of-prepayment-PA, applied ≤
        # prepaid) ever run — allowing pa_type through here would let a caller
        # skip them entirely (e.g. settle someone else's PO-based prepayment PA
        # by routing through an unrelated agreement). Only the plain-pay type is
        # meaningful without a PO to prepay against or settle.
        if body.pa_type != "regular":
            raise HTTPException(
                status_code=422,
                detail="Only pa_type='regular' can be raised against an agreement "
                       "(prepayment/settlement/balance require a PO).",
            )
        # The agreement itself is the PA's authorisation — it must actually be
        # approved (or still inside its post-expiry grace window) before it can
        # back a payment. Same admission rule invoices are matched under.
        if not await agr_crud.is_admissible(db, agr):
            raise HTTPException(
                status_code=422,
                detail=f"Agreement {agr.number} is not active (status={agr.status}) "
                       "or is past its grace window — a PA cannot be raised against it.",
            )
        # There is no receipt gate on this route (never any GR) — the linked
        # invoice(s) are the ONLY evidence this PA pays real, already-billed
        # spend rather than an arbitrary amount against an approved ceiling.
        # Every listed invoice must actually be matched to THIS agreement,
        # cleared for payment, and (recurring) linked to a confirmed period —
        # see _validate_agreement_pa_invoices, shared with update_pa's PATCH
        # path so the same rules apply there too.
        await _validate_agreement_pa_invoices(db, agr, body.invoice_ids)
        # 收货闸门不适用:协议路线定义上就没有 GR(1A 无 pickup slip,1B 才有)。
        created = await pa_crud.create(
            db, body,
            po_number=None,
            agreement_number=agr.number,
            vendor_id=agr.vendor_id,
            vendor_name=agr.vendor_name,
            created_by=uuid.UUID(user["sub"]),
        )
        return created

    # ── PO route below, unchanged ──────────────────────────────────────────────
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
            roles = await _effective_role_codes(db, "requester", uuid.UUID(user["sub"]))
            if not _may_create_pa_on_behalf(roles, po):
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
        # Cannot apply more prepayment than was actually prepaid on the original PA.
        if applied > orig.payment_amount:
            raise HTTPException(
                status_code=422,
                detail=f"prepayment_applied ({applied}) exceeds the amount actually "
                       f"prepaid on {orig.pa_number} ({orig.payment_amount}). "
                       "You cannot apply more prepayment than was paid.",
            )
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

    # ── 收货闸门 —— 预付款先付后收豁免;其余类型须有 3-way matched 发票 ──
    scope = await build_scope(db, user)
    if body.pa_type != "prepayment":
        if not await po_crud.po_has_three_way_matched_invoice(db, body.po_id):
            if not body.receipt_override:
                raise HTTPException(
                    status_code=422,
                    detail="No 3-way matched invoice for this PO (a matched invoice "
                           "with a linked goods receipt). Create a goods receipt "
                           "first, or override with a reason.",
                )
            if not scope["perms"].get("pa_override_receipt", False):
                raise HTTPException(
                    status_code=403,
                    detail="You are not authorized to create a payment without goods receipt.",
                )
            if not (body.receipt_override_reason or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail="A reason is required to override the goods-receipt requirement.",
                )

    created = await pa_crud.create(
        db, body,
        po_number=po.number,
        vendor_id=po.vendor_id,
        vendor_name=po.vendor_name,
        created_by=uuid.UUID(user["sub"]),
        receipt_override=body.receipt_override,
        receipt_override_reason=body.receipt_override_reason,
        receipt_override_by=uuid.UUID(user["sub"]) if body.receipt_override else None,
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
    if pa is None or (pa.po_id is None and pa.agreement_id is None):  # OA Direct PA (both NULL) — not agreement PAs (agreement_id set)
        raise HTTPException(status_code=404, detail="PA not found")
    scope = await build_scope(db, user)
    if not await is_pa_visible(db, pa, scope):
        raise HTTPException(status_code=404, detail="PA not found")
    return pa


@router.patch("/{pa_id}", response_model=PaResponse)
async def update_pa(pa_id: uuid.UUID, body: PaUpdate, db: SessionDep, user: PaWriteDep):
    pa = await pa_crud.get_by_id(db, pa_id)
    if pa is None or (pa.po_id is None and pa.agreement_id is None):  # OA Direct PA (both NULL) — not agreement PAs (agreement_id set)
        raise HTTPException(status_code=404, detail="PA not found")
    if pa.status not in ("draft", "returned"):
        raise HTTPException(status_code=409, detail=f"Cannot edit PA in status '{pa.status}'")
    # Whole-branch review finding: pa_crud.update() assigns invoice_ids
    # unconditionally, with none of create_pa's agreement gates re-run — a
    # PATCH could swap in an invoice that is unconfirmed, unlinked to a
    # billing period, or matched to a different agreement entirely, and every
    # check below would be bypassed. agreement_id itself is immutable here
    # (PaUpdate has no such field), so only invoice_ids needs re-validating.
    if pa.agreement_id is not None and body.invoice_ids is not None:
        agr = await agr_crud.get_by_id(db, pa.agreement_id)
        if agr is None:
            raise HTTPException(status_code=404, detail="Agreement not found")
        await _validate_agreement_pa_invoices(db, agr, body.invoice_ids)
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
    if pa is None or (pa.po_id is None and pa.agreement_id is None):  # OA Direct PA (both NULL) — not agreement PAs (agreement_id set)
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
    if pa is None or (pa.po_id is None and pa.agreement_id is None):  # OA Direct PA (both NULL) — not agreement PAs (agreement_id set)
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
    if pa is None or (pa.po_id is None and pa.agreement_id is None):  # OA Direct PA (both NULL) — not agreement PAs (agreement_id set)
        raise HTTPException(status_code=404, detail="PA not found")
    return await pa_crud.get_approval_events(db, pa_id)


@router.get("/{pa_id}/workflow-steps")
async def pa_workflow_steps(pa_id: uuid.UUID, user: CurrentUserPayload, token: BearerToken):
    try:
        return await approval_client.get_workflow_steps("pa", str(pa_id), token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
