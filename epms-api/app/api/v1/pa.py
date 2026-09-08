"""Payment Application endpoints."""
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from sqlalchemy import select

from app.core.authz import require_permission
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.core.access_scope import (
    build_scope,
    is_agreement_visible,
    is_pr_in_departments,
    _effective_role_codes,
    _user_dept_id,
)
from app.services import approval_client as approval_client
from app.services.approval_client import delegate_action
from app.services import finance_client
from app.crud import agreement as agr_crud
from app.crud import pa as pa_crud
from app.crud import po as po_crud
from app.crud.pa_links import pa_ids_for_po, pa_ids_for_pos
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
               Invoice.schedule_id, Invoice.receipt_ids, Invoice.legacy_settlement)
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
    # milestone 本期没有验收闸门(设计 §5.3),house_account 走凭证路径(见下方
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
    # house_account 免收货,凭证就是柜台小票/送货单/服务单(receipt_type)。1A 时
    # 没有凭证可挂,所以每张发票都被标成 legacy —— 那些存量数据必须继续放行,
    # 否则本期改动会卡死历史。pa.py:94 那句 "house_account 走凭证路径" 的注释
    # 从此才是真的。
    if agr.agreement_type == "house_account":
        unsupported = [
            r.internal_ref for r in rows
            if not (r.receipt_ids or r.legacy_settlement)
        ]
        if unsupported:
            raise HTTPException(
                status_code=422,
                detail=(f"No receipts are attached to {', '.join(unsupported)}. "
                        "Match the invoice to the receipts it covers, or settle it "
                        "explicitly without receipt evidence, before raising payment."))


async def _assert_invoices_belong_to_pos(
    db: SessionDep, pos: list[PurchaseOrder], invoice_ids: list[uuid.UUID],
) -> None:
    """Every listed invoice must belong to one of the PA's purchase orders.

    An invoice "belongs to" the PA if it is header-linked (Invoice.po_id) OR
    allocated via the multi-PO allocation table — to ANY of the POs the PA
    covers. The invoice list the user picks from uses the same OR rule
    (crud.invoice.get_all), so a header-only check would wrongly 422 an invoice
    whose primary PO differs from this one but is allocated here.

    Shared by create_pa and update_pa's PO-set replacement: dropping a PO from a
    draft leaves any invoice that belonged only to it stranded on the payment,
    and nothing else would notice — the payment would go out settling an
    invoice for an order it no longer covers.
    """
    if not invoice_ids:
        return
    po_ids = [p.id for p in pos]
    rows = (await db.execute(
        select(Invoice.id, Invoice.po_id).where(Invoice.id.in_(invoice_ids))
    )).all()
    found_ids = {r.id for r in rows}
    not_found = len(set(invoice_ids)) - len(found_ids)
    if not_found:
        raise HTTPException(status_code=422, detail=f"{not_found} invoice(s) not found")

    direct_ids = {r.id for r in rows if r.po_id in set(po_ids)}
    need_alloc_check = found_ids - direct_ids
    if need_alloc_check:
        alloc_rows = await db.execute(
            select(InvoicePoAllocation.invoice_id).where(
                InvoicePoAllocation.invoice_id.in_(need_alloc_check),
                InvoicePoAllocation.po_id.in_(po_ids),
            ).distinct()
        )
        need_alloc_check -= set(alloc_rows.scalars().all())
    if need_alloc_check:
        raise HTTPException(
            status_code=422,
            detail=f"Invoice(s) do not belong to {', '.join(p.number for p in pos)}: "
                   f"{', '.join(str(i) for i in need_alloc_check)}",
        )


async def _assert_pos_coherent(pos: list[PurchaseOrder], pa_type: str) -> None:
    """The cross-PO rules for one payment application.

    Shared by create_pa and update_pa's PO-set replacement so a PA can never be
    edited into a state creation would have refused.

    The PA header carries ONE vendor and ONE currency and authorises ONE
    transfer, so mixing either is refused outright rather than resolved by
    quietly taking the primary PO's value — that would pay the wrong party, or
    add up amounts that are not commensurable.
    """
    if len({p.vendor_id for p in pos}) > 1:
        raise HTTPException(
            status_code=422,
            detail="All purchase orders on one payment application must belong to the "
                   "same vendor. Raise a separate payment per vendor.",
        )
    if len({(p.currency or "CAD") for p in pos}) > 1:
        raise HTTPException(
            status_code=422,
            detail="All purchase orders on one payment application must share the same "
                   "currency. Raise a separate payment per currency.",
        )
    # Prepayment / settlement / balance are defined against ONE order: the cap
    # comes from that PO's vendor, "one open prepayment per PO" is per-PO, and a
    # settlement reconciles exactly one earlier prepayment against it. None of
    # them has a coherent multi-PO reading, so multi-PO is regular-only.
    if len(pos) > 1 and pa_type != "regular":
        raise HTTPException(
            status_code=422,
            detail="Only a regular payment can cover several purchase orders. "
                   "Prepayment, settlement and balance payments are raised against a "
                   "single purchase order.",
        )
    # Departments are deliberately NOT constrained. They were, briefly, for a
    # good reason — approval-api resolves a PA's department-scoped approvers
    # from its PRIMARY PO alone, so combining departments used to route one
    # department's spend past its own reviewer. But the constraint could not
    # survive contact with how vendors actually invoice: one invoice routinely
    # covers purchase orders from several departments, the vendor has no idea
    # where those boundaries are, and forcing a payment per department split
    # exactly the document finance needs to review as a whole.
    #
    # The routing problem is now solved where it lives, in approval-api: a PA
    # spanning departments auto-skips the Department Manager and Director steps
    # (visibly, with a reason on the timeline) and its GM/OPM step resolves from
    # an engine-wide setting rather than from a department that cannot be
    # picked. See engine.py::_should_skip_step / _resolve_gm_or_opm.


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
    await pa_crud.attach_po_links(db, items)
    return {"items": [PaResponse.model_validate(pa) for pa in items], "total": total}


async def _get_prepayment_config(db: SessionDep) -> PrepaymentConfig:
    cfg_row = await db.execute(select(CompanyConfig).limit(1))
    cfg = cfg_row.scalar_one_or_none()
    raw = cfg.prepayment_config if cfg and cfg.prepayment_config else {}
    return PrepaymentConfig.model_validate(raw)


async def _may_create_pa_on_behalf(
    db: SessionDep,
    roles: set[str],
    po: PurchaseOrder,
    user_id: uuid.UUID,
) -> bool:
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
    if "erp_pa_officer" in roles and po.pr_id is None and po.source == "nc":
        return True
    # A Department Administrator raises payments for the department they
    # administer — their own department's requisitions, and no further. Unlike
    # the two roles above this is a SCOPED exemption, because dept_admin is a
    # RESTRICTED role (access_scope._RESTRICTED_ROLES): the department boundary
    # is the whole of its authority, so the same two conditions that decide
    # whether she may SEE the requisition decide whether she may pay it.
    #
    # Without this the matrix and the code disagree: ticking "Create / Edit PAs"
    # for dept_admin in the Access Control Matrix grants nothing at all to the
    # normal shape of that role (dept_admin layered on a `requester` login), as
    # every colleague's PO is rejected right here — while the SAME permission
    # set held with dept_admin as the PRIMARY role skips this branch entirely
    # and pays company-wide. Neither outcome was intended by anyone ticking that
    # box.
    if "dept_admin" in roles and po.pr_id is not None:
        own_dept = await _user_dept_id(db, user_id)
        return await is_pr_in_departments(db, po.pr_id, {own_dept} if own_dept else set())
    return False


@router.post("", response_model=PaResponse, status_code=201)
async def create_pa(body: PaCreate, db: SessionDep, user: PaWriteDep, token: BearerToken):
    # ── Agreement route: no PO, no GR, no receipt gate ─────────────────────────
    if body.agreement_id is not None:
        agr = await agr_crud.get_by_id(db, body.agreement_id)
        if agr is None:
            raise HTTPException(status_code=404, detail="Agreement not found")
        # Holding epms.pa.write says you raise payments; it does not say you
        # raise them against THIS agreement. `requester` holds that key in the
        # default matrix, so without this an employee with no connection to a
        # house account — not its creator, not its owner, not in its department
        # — could raise a payment application against it. The agreement is the
        # authorisation for its own payments, so the same row scope that decides
        # whether you can see it decides whether you can spend it.
        if not await is_agreement_visible(db, agr.id, await build_scope(db, user)):
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
        # 收货闸门不适用:协议路线定义上就没有 GR(1A 无凭证,1B 才有)。
        created = await pa_crud.create(
            db, body,
            po_number=None,
            agreement_number=agr.number,
            vendor_id=agr.vendor_id,
            vendor_name=agr.vendor_name,
            created_by=uuid.UUID(user["sub"]),
        )
        return created

    # ── PO route below ─────────────────────────────────────────────────────────
    # One PA may settle several POs of the same vendor in a single payment.
    # `pos` is the whole set, primary first; `po` stays bound to the primary so
    # the single-PO guards further down read exactly as they always did.
    po_ids = body.resolved_po_ids
    pos: list[PurchaseOrder] = []
    for pid in po_ids:
        loaded = await po_crud.get_by_id(db, pid)
        if loaded is None:
            raise HTTPException(status_code=404, detail="Purchase order not found")
        pos.append(loaded)
    po = pos[0]

    await _assert_pos_coherent(pos, body.pa_type)

    # A plain requester may only pay against POs linked to a PR they raised. The
    # PA write role list includes 'requester', and a requester carrying a special
    # role assignment (e.g. finance_bp) would otherwise have unrestricted PO scope,
    # so enforce ownership here on the JWT base role regardless of that widening.
    # Checked for EVERY PO on the payment: passing on the primary alone would let
    # anyone staple someone else's order onto their own PA and pay it.
    if user.get("role") == "requester":
        roles = await _effective_role_codes(db, "requester", uuid.UUID(user["sub"]))
        for p in pos:
            pr_requester_id = None
            if p.pr_id is not None:
                pr_requester_id = (await db.execute(
                    select(PurchaseRequest.created_by).where(PurchaseRequest.id == p.pr_id)
                )).scalar_one_or_none()
            if pr_requester_id != uuid.UUID(user["sub"]):
                if not await _may_create_pa_on_behalf(db, roles, p, uuid.UUID(user["sub"])):
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
                PaymentApplication.id.in_(pa_ids_for_po(po.id)),
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
                PaymentApplication.id.in_(pa_ids_for_po(po.id)),
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
        # An open prepayment on ANY of the POs blocks this regular payment —
        # checking only the primary would let an unsettled advance ride along on
        # a second PO and be paid for twice.
        blocking = (await db.execute(
            select(PaymentApplication.pa_number).where(
                PaymentApplication.id.in_(pa_ids_for_pos([p.id for p in pos])),
                PaymentApplication.pa_type == "prepayment",
                PaymentApplication.status.notin_(["cancelled", "rejected", "processed"]),
            )
        )).scalars().first()
        if blocking:
            raise HTTPException(
                status_code=409,
                detail=f"Prepayment PA {blocking} is still open. Settle or cancel it before creating a regular PA.",
            )

    # ── Invoice validation ─────────────────────────────────────────────────────
    await _assert_invoices_belong_to_pos(db, pos, body.invoice_ids)

    # ── 收货闸门 —— 预付款先付后收豁免;其余类型须有 3-way matched 发票 ──
    # 每一张 PO 都要过闸:只查主 PO 会让「搭车」的第二张 PO 在完全没收货的情况下
    # 被付掉,而这正是闸门要拦的事。override 是整张 PA 一次性的决定,理由里报出
    # 具体是哪几张 PO 没有凭证。
    scope = await build_scope(db, user)
    if body.pa_type != "prepayment":
        ungated = [
            p for p in pos
            if not await po_crud.po_has_three_way_matched_invoice(db, p.id)
        ]
        if ungated:
            numbers = ", ".join(p.number for p in ungated)
            if not body.receipt_override:
                raise HTTPException(
                    status_code=422,
                    detail=f"No 3-way matched invoice for {numbers} (a matched invoice "
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
        po_links=[(p.id, p.number) for p in pos],
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

    # ── PO-set replacement ─────────────────────────────────────────────────────
    # Editing which POs a draft covers runs the same gates creation does. Doing
    # it here rather than in crud keeps one statement of the rules: a PA must
    # never be reachable by PATCH in a shape POST would have refused.
    po_links: list[tuple[uuid.UUID, str]] | None = None
    if body.po_ids is not None:
        if pa.agreement_id is not None:
            raise HTTPException(
                status_code=422,
                detail="An agreement-backed payment application has no purchase orders.",
            )
        # De-dup, order preserved: the first entry becomes the primary PO.
        new_ids: list[uuid.UUID] = []
        for pid in body.po_ids:
            if pid not in new_ids:
                new_ids.append(pid)
        if not new_ids:
            raise HTTPException(
                status_code=422,
                detail="A payment application must cover at least one purchase order.",
            )
        pos: list[PurchaseOrder] = []
        for pid in new_ids:
            loaded = await po_crud.get_by_id(db, pid)
            if loaded is None:
                raise HTTPException(status_code=404, detail="Purchase order not found")
            pos.append(loaded)
        await _assert_pos_coherent(pos, pa.pa_type)
        # The invoice list to check against the NEW PO set is the one being
        # saved, or the PA's existing one when the caller only changed the POs.
        # Dropping a PO without this leaves that PO's invoice on the payment.
        invoice_ids_after = (
            body.invoice_ids if body.invoice_ids is not None
            else [uuid.UUID(i) for i in (pa.invoice_ids or [])]
        )
        await _assert_invoices_belong_to_pos(db, pos, invoice_ids_after)
        if user.get("role") == "requester":
            roles = await _effective_role_codes(db, "requester", uuid.UUID(user["sub"]))
            for p in pos:
                pr_requester_id = None
                if p.pr_id is not None:
                    pr_requester_id = (await db.execute(
                        select(PurchaseRequest.created_by).where(PurchaseRequest.id == p.pr_id)
                    )).scalar_one_or_none()
                if pr_requester_id != uuid.UUID(user["sub"]):
                    if not await _may_create_pa_on_behalf(db, roles, p, uuid.UUID(user["sub"])):
                        raise HTTPException(
                            status_code=403,
                            detail="You can only create payments for purchase orders linked to your own requisitions.",
                        )
        # A PO dropped from the PA is no longer being paid by it. If nothing
        # else pays it, its "Create Payment Application" prompt has to come
        # back — otherwise removing a PO here strands it exactly the way a
        # Data Maintenance delete used to (admin/registry.py does the same).
        removed = [pid for pid in (pa.po_ids or []) if pid not in set(new_ids)]
        po_links = [(p.id, p.number) for p in pos]
        updated = await pa_crud.update(db, pa, body, po_links=po_links)
        for pid in removed:
            await pa_crud.reopen_create_pa_tasks_if_unpaid(db, pid)
        return updated

    return await pa_crud.update(db, pa, body)


def _assert_invoice_link_on_submit(pa) -> None:
    """A PO-based PA must name the invoice it settles before it enters approval.

    pa.invoice_ids is the only place that link exists, and four things read it:
    finance-api's payment executor closes the invoice and its ap_invoices row
    from it, the remittance advice takes the vendor invoice numbers from it,
    and the create-PA screen locks an invoice against a second PA by it. An
    empty array no-ops all of them at once — the cash leaves, the invoice stays
    open in AP, the advice is blocked 'missing_invoice_no', and nothing stops
    the same invoice being paid again by another PA.

    None of that is repairable afterwards: update_pa above accepts only
    draft/returned, so the omission becomes permanent the moment the PA is
    approved. That is why this refuses entry to approval rather than warning.

    On the action, not in PaCreate: a draft must still be savable
    half-finished. (Same placement, and same reason, as the PR budget-code
    gate — putting it in the schema would block the draft that shares it.)

    Exemptions, both structural rather than discretionary:
      * prepayment PAs — paid before the vendor has invoiced anything;
      * agreement PAs (po_id NULL) — gated by _validate_agreement_pa_invoices.
    """
    if pa.po_id is None or pa.pa_type == "prepayment" or pa.invoice_ids:
        return
    raise HTTPException(
        status_code=409,
        detail=(
            "This payment application links no invoice. Open it, tick the "
            f"invoice(s) on {pa.po_number} that it settles, and submit again. "
            "Only prepayments may be submitted without one."
        ),
    )


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
    if body.action == "submit":
        _assert_invoice_link_on_submit(pa)
    try:
        if body.action == "process":
            # Payment is not a workflow action (Phase 0-B1.5): forward to
            # finance-api's unified executor — it owns can_pay, the status
            # flip, payment_records, invoice marking and the posting event.
            # body.credit_ids is three-valued and passed through untouched:
            # None (the Process dialog was left alone) keeps finance-api's
            # automatic FIFO default; a list is the operator's explicit choice
            # after deselecting credits in that dialog.
            result = await finance_client.execute_payment(
                doc_kind="pa", doc_id=pa_id, bearer_token=token,
                notes=body.comment, bank_account_id=body.bank_account_id,
                credit_ids=body.credit_ids,
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
        from app.crud.signatories import approval_signatories
        requester_name, approvals = await approval_signatories(
            db, "pa", pa_id, pa.created_by
        )
        loop = asyncio.get_event_loop()
        pdf_bytes = await loop.run_in_executor(
            None, generate_pa_pdf, pa, company_name,
            cfg.pdf_templates if cfg else None,
            cfg.logo_data_url if cfg else None,
            requester_name, approvals,
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
