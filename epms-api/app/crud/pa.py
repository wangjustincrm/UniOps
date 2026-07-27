"""CRUD + workflow for Payment Application (PA)."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._numbering import next_number
from app.models.approval import ApprovalEvent
from app.models.config import CompanyConfig
from app.models.cost_center import CostCenter
from app.models.invoice import Invoice
from app.models.pa import PaLineItem, PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.pa_attachment import PaAttachment
from app.models.task import Task
from app.models.user import User
from app.schemas.pa import PA_WORKFLOW, PaActionRequest, PaCreate, PaUpdate
from app.schemas.pr import ApprovalEventResponse
from app.services.pdf_pa import generate_pa_pdf


# ── Number generation ──────────────────────────────────────────────────────────

async def _next_number(db: AsyncSession) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"PA-{today}-"
    return await next_number(db, PaymentApplication.pa_number, prefix, width=4)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_line_items(pa_id: uuid.UUID, items_in) -> list[PaLineItem]:
    return [
        PaLineItem(
            pa_id=pa_id,
            po_line_id=item.po_line_id,
            description=item.description,
            qty=item.qty,
            unit=item.unit,
            unit_price=item.unit_price,
            line_total=item.line_total,
            notes=item.notes,
            sort_order=i,
        )
        for i, item in enumerate(items_in)
    ]


def _compute_payment(payload) -> Decimal:
    applied = getattr(payload, "prepayment_applied", None) or Decimal("0")
    net = (
        payload.subtotal
        + payload.tax_amount
        + payload.shipping_amount
        + payload.other_charges
        - applied
    )
    return net if net > Decimal("0") else Decimal("0")


# ── Reads ──────────────────────────────────────────────────────────────────────

async def get_all(
    db: AsyncSession,
    *,
    status: str | None = None,
    po_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    search: str | None = None,
    po_ids_subq=None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[PaymentApplication], int]:
    from sqlalchemy import or_
    # EPMS owns PO-based PAs only; OA's Direct PAs (NULL po_id) live in the same
    # shared table but belong to expense-api. Excluding them keeps PaResponse
    # (po_id/po_number non-nullable) valid and avoids cross-domain bleed.
    q = select(PaymentApplication).where(PaymentApplication.po_id.is_not(None))
    if po_ids_subq is not None:
        if created_by:
            q = q.where(or_(
                PaymentApplication.po_id.in_(po_ids_subq),
                PaymentApplication.created_by == created_by,
            ))
        else:
            q = q.where(PaymentApplication.po_id.in_(po_ids_subq))
    elif created_by:
        q = q.where(PaymentApplication.created_by == created_by)
    if status:
        q = q.where(PaymentApplication.status == status)
    if po_id:
        q = q.where(PaymentApplication.po_id == po_id)
    if vendor_id:
        q = q.where(PaymentApplication.vendor_id == vendor_id)
    if search:
        # Match what the UI advertises: PA #, vendor name, PO # (all snapshot
        # columns on the PA row — no join needed; NULL po_number never matches).
        term = f"%{search}%"
        q = q.where(
            PaymentApplication.pa_number.ilike(term)
            | PaymentApplication.vendor_name.ilike(term)
            | PaymentApplication.po_number.ilike(term)
        )
    if department_id:
        # PA has no cost center — resolve department via PA → PO → PR → cost center.
        q = q.where(PaymentApplication.po_id.in_(
            select(PurchaseOrder.id).where(PurchaseOrder.pr_id.in_(
                select(PurchaseRequest.id).where(PurchaseRequest.cost_center_id.in_(
                    select(CostCenter.id).where(CostCenter.department_id == department_id)
                ))
            ))
        ))
    total: int = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    offset = (page - 1) * page_size
    items = list((await db.execute(
        q.order_by(PaymentApplication.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all())
    return items, total


async def get_by_id(db: AsyncSession, pa_id: uuid.UUID) -> PaymentApplication | None:
    result = await db.execute(
        select(PaymentApplication, User.full_name)
        .outerjoin(User, User.id == PaymentApplication.created_by)
        .where(PaymentApplication.id == pa_id)
    )
    row = result.first()
    if row is None:
        return None
    pa, creator_name = row
    pa.created_by_name = creator_name  # transient attr consumed by PaResponse
    return pa


# ── Create ─────────────────────────────────────────────────────────────────────

async def create(
    db: AsyncSession,
    payload: PaCreate,
    po_number: str,
    vendor_id: uuid.UUID,
    vendor_name: str,
    created_by: uuid.UUID,
) -> PaymentApplication:
    number = await _next_number(db)
    payment_amount = _compute_payment(payload)

    pa = PaymentApplication(
        pa_number=number,
        title=payload.title,
        po_id=payload.po_id,
        po_number=po_number,
        vendor_id=vendor_id,
        vendor_name=vendor_name,
        invoice_ids=[str(i) for i in payload.invoice_ids],
        gr_ids=[str(i) for i in payload.gr_ids],
        pa_type=payload.pa_type,
        prepayment_pa_id=getattr(payload, "prepayment_pa_id", None),
        prepayment_applied=getattr(payload, "prepayment_applied", None),
        subtotal=payload.subtotal,
        tax_amount=payload.tax_amount,
        tax_code=payload.tax_code,
        tax_rate=payload.tax_rate,
        shipping_amount=payload.shipping_amount,
        other_charges=payload.other_charges,
        other_charges_note=payload.other_charges_note,
        payment_amount=payment_amount,
        currency=payload.currency,
        notes=payload.notes,
        prepayment_pct=payload.prepayment_pct,
        expected_settlement_date=payload.expected_settlement_date,
        settlement_status="pending" if payload.pa_type == "prepayment" else None,
        created_by=created_by,
    )
    db.add(pa)
    await db.flush()

    for item in _build_line_items(pa.id, payload.line_items):
        db.add(item)

    # A PA now exists for this PO — clear any open "Create Payment Application"
    # prompts for it (the task previously lingered because this was never called).
    await _complete_create_pa_tasks(db, payload.po_id)
    # A prepayment PA additionally satisfies the "Create Prepayment PA" prompt.
    if pa.pa_type == "prepayment":
        await _complete_create_prepayment_pa_tasks(db, payload.po_id)

    await db.flush()
    await db.refresh(pa)
    return pa


# ── Update (draft only) ───────────────────────────────────────────────────────

async def update(
    db: AsyncSession, pa: PaymentApplication, payload: PaUpdate
) -> PaymentApplication:
    for field in ("title", "notes", "other_charges_note",
                  "prepayment_pct", "expected_settlement_date", "tax_code", "tax_rate"):
        val = getattr(payload, field)
        if val is not None:
            setattr(pa, field, val)

    if payload.invoice_ids is not None:
        pa.invoice_ids = [str(i) for i in payload.invoice_ids]
    if payload.gr_ids is not None:
        pa.gr_ids = [str(i) for i in payload.gr_ids]

    # Recompute payment amount if financials changed
    for field in ("subtotal", "tax_amount", "shipping_amount", "other_charges", "prepayment_applied"):
        val = getattr(payload, field)
        if val is not None:
            setattr(pa, field, val)

    applied = pa.prepayment_applied or Decimal("0")
    net = pa.subtotal + pa.tax_amount + pa.shipping_amount + pa.other_charges - applied
    pa.payment_amount = net if net > Decimal("0") else Decimal("0")

    if payload.line_items is not None:
        for old in list(pa.line_items):
            await db.delete(old)
        await db.flush()
        for item in _build_line_items(pa.id, payload.line_items):
            db.add(item)

    await db.flush()
    await db.refresh(pa)
    return pa


# ── Workflow ───────────────────────────────────────────────────────────────────

async def _get_config(db: AsyncSession) -> CompanyConfig | None:
    result = await db.execute(select(CompanyConfig).limit(1))
    return result.scalar_one_or_none()


async def _get_pa_workflow(db: AsyncSession) -> list[dict]:
    """Load PA workflow from company config; falls back to hardcoded PA_WORKFLOW."""
    cfg = await _get_config(db)
    if cfg and cfg.workflow_defs:
        nodes = cfg.workflow_defs.get("pa", [])
        if nodes:
            return nodes
    return PA_WORKFLOW


# NOTE (Phase a A0): the legacy action() workflow function was removed —
# approval actions delegate to approval-api and payment (process) goes to
# finance-api's unified executor. Its private task helpers below are kept
# until a follow-up cleanup confirms no other callers.


def _settlement_gross(pa: PaymentApplication) -> Decimal:
    """Full charge (final invoice total) of a settlement PA, before the
    prepayment deduction."""
    return pa.subtotal + pa.tax_amount + pa.shipping_amount + pa.other_charges


def _settlement_variance(pa: PaymentApplication, prepaid: Decimal) -> Decimal:
    """final invoice − original prepaid amount. >0 = balance was owed (paid by
    this settlement); <0 = overpaid (credit note expected); 0 = exact match."""
    return _settlement_gross(pa) - prepaid


async def mark_prepayment_settled(
    db: AsyncSession,
    settlement_pa: PaymentApplication,
    actor_id: uuid.UUID,
) -> None:
    """Reconcile a settlement PA's source prepayment PA.

    Variance = final invoice − prepaid applied (the true over/under, which may be
    negative when overpaid — distinct from payment_amount that is clamped at 0).
    Idempotent: skips if the source prepayment is missing or already settled.
    """
    if settlement_pa.prepayment_pa_id is None:
        return
    orig = await get_by_id(db, settlement_pa.prepayment_pa_id)
    if orig is None or orig.pa_type != "prepayment":
        return
    if orig.settlement_status == "settled":
        return
    settler = await db.execute(select(User.full_name).where(User.id == actor_id))
    variance = _settlement_variance(settlement_pa, orig.payment_amount)
    orig.settlement_status = "settled"
    orig.settled_at = datetime.now(timezone.utc)
    orig.settled_by = actor_id
    orig.settled_by_name = settler.scalar_one_or_none()
    orig.settlement_variance = variance
    note = f"Settled via {settlement_pa.pa_number}"
    if variance < 0:
        note += f" — overpaid by {abs(variance)}, credit note expected"
    orig.settlement_note = note
    await db.flush()


async def finalize_settlement_reconciliation(
    db: AsyncSession,
    settlement_pa: PaymentApplication,
    actor_id: uuid.UUID,
) -> None:
    """Close a zero-cash (net payable == 0) settlement PA: no payment is made —
    the prepayment already covered the invoice. Marks the settlement processed,
    the linked invoices paid, reconciles the prepayment, and clears open tasks.
    Finance-owned ap_invoices are flipped separately by the API layer (fail-open).
    """
    settlement_pa.status = "processed"
    await _mark_invoices_paid(db, settlement_pa)
    await mark_prepayment_settled(db, settlement_pa, actor_id)
    await _complete_tasks(db, "pa", settlement_pa.id)
    await db.flush()
    await db.refresh(settlement_pa)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _attach_pa_pdf(db: AsyncSession, pa: PaymentApplication, company_name: str) -> None:
    """Generate an approved-PA PDF and store it as an attachment."""
    import asyncio
    loop = asyncio.get_running_loop()
    pdf_bytes = await loop.run_in_executor(None, generate_pa_pdf, pa, company_name)
    db.add(PaAttachment(
        pa_id=pa.id,
        filename=f"{pa.pa_number}.pdf",
        content_type="application/pdf",
        file_size=len(pdf_bytes),
        file_data=pdf_bytes,
    ))


async def _mark_invoices_paid(db: AsyncSession, pa: PaymentApplication) -> None:
    for inv_id_str in pa.invoice_ids:
        try:
            inv_id = uuid.UUID(inv_id_str)
        except ValueError:
            continue
        result = await db.execute(select(Invoice).where(Invoice.id == inv_id))
        inv = result.scalar_one_or_none()
        if inv and inv.status in ("matched", "approved"):
            inv.status = "paid"


async def _create_task(db: AsyncSession, pa: PaymentApplication, step: int, workflow: list[dict]) -> None:
    wf = workflow[step]
    db.add(Task(
        type="approve_pa",
        priority="normal",
        document_type="pa",
        document_id=pa.id,
        document_number=pa.pa_number,
        assigned_role=wf["role"],
        title=f"Approve PA: {pa.pa_number} — {pa.title}",
        description=f"Step {step + 1}/{len(workflow)}: {wf['label']} review required.",
        amount=pa.payment_amount,
        vendor=pa.vendor_name,
    ))


async def _create_process_pa_task(db: AsyncSession, pa: PaymentApplication) -> None:
    db.add(Task(
        type="process_pa",
        priority="normal",
        document_type="pa",
        document_id=pa.id,
        document_number=pa.pa_number,
        assigned_role="ap_clerk",
        title=f"Process Payment: {pa.pa_number} — {pa.title}",
        description=f"PA {pa.pa_number} has been fully approved. Please process the payment and mark as processed.",
        amount=pa.payment_amount,
        vendor=pa.vendor_name,
    ))


async def _create_revise_task(db: AsyncSession, pa: PaymentApplication) -> None:
    db.add(Task(
        type="revise_pa",
        priority="normal",
        document_type="pa",
        document_id=pa.id,
        document_number=pa.pa_number,
        assigned_role="finance_bp",
        assigned_user_id=pa.created_by,
        title=f"Revise PA: {pa.pa_number} — {pa.title}",
        description="Your PA has been returned for revision.",
        amount=pa.payment_amount,
        vendor=pa.vendor_name,
    ))


async def create_settlement_confirm_task(
    db: AsyncSession, pa: PaymentApplication, variance: Decimal
) -> None:
    """Zero-cash settlement with a non-zero variance (overpaid) needs a finance
    sign-off before it reconciles — no payment, just confirm the variance / credit
    note. (Exact-match net-0 settlements auto-reconcile without a task.)"""
    db.add(Task(
        type="confirm_settlement",
        priority="normal",
        document_type="pa",
        document_id=pa.id,
        document_number=pa.pa_number,
        assigned_role="finance_bp",
        title=f"Confirm Settlement: {pa.pa_number} — {pa.title}",
        description=(
            f"Prepayment reconciliation with a variance of {variance} "
            f"(overpaid — credit note expected). Confirm to settle; no payment will be made."
        ),
        amount=pa.payment_amount,
        vendor=pa.vendor_name,
    ))
    await db.flush()


async def _complete_tasks(db: AsyncSession, doc_type: str, doc_id: uuid.UUID) -> None:
    result = await db.execute(
        select(Task).where(
            Task.document_type == doc_type,
            Task.document_id == doc_id,
            Task.is_completed.is_(False),
        )
    )
    now = datetime.now(timezone.utc)
    for task in result.scalars().all():
        task.is_completed = True
        task.completed_at = now


async def _complete_create_pa_tasks(db: AsyncSession, po_id: uuid.UUID) -> None:
    """Complete pending create_pa tasks once a PA exists for the given PO.

    create_pa tasks come from two sources with different document anchors:
      - invoice match  → document_type="po",  document_id=<po_id>
      - GR completion  → document_type="gr",  document_id=<gr_id of a GR on this PO>
    Both are just a prompt to "create a PA for this PO", so creating any PA for the
    PO satisfies them — clear both anchors.
    """
    from app.models.gr import GoodsReceipt

    gr_ids_subq = select(GoodsReceipt.id).where(GoodsReceipt.po_id == po_id)
    result = await db.execute(
        select(Task).where(
            Task.type == "create_pa",
            Task.is_completed.is_(False),
            or_(
                and_(Task.document_type == "po", Task.document_id == po_id),
                and_(Task.document_type == "gr", Task.document_id.in_(gr_ids_subq)),
            ),
        )
    )
    now = datetime.now(timezone.utc)
    for task in result.scalars().all():
        task.is_completed = True
        task.completed_at = now


async def _complete_create_prepayment_pa_tasks(db: AsyncSession, po_id: uuid.UUID) -> None:
    """Complete pending create_prepayment_pa tasks for the PO (document_type='po').

    Called only when the PA being created is itself a prepayment, so a regular PA
    never clears the advance-payment prompt.
    """
    result = await db.execute(
        select(Task).where(
            Task.type == "create_prepayment_pa",
            Task.document_type == "po",
            Task.document_id == po_id,
            Task.is_completed.is_(False),
        )
    )
    now = datetime.now(timezone.utc)
    for task in result.scalars().all():
        task.is_completed = True
        task.completed_at = now


async def get_approval_events(
    db: AsyncSession, pa_id: uuid.UUID
) -> list[ApprovalEventResponse]:
    result = await db.execute(
        select(ApprovalEvent, User.full_name)
        .outerjoin(User, User.id == ApprovalEvent.actor_id)
        .where(ApprovalEvent.document_type == "pa", ApprovalEvent.document_id == pa_id)
        .order_by(ApprovalEvent.created_at)
    )
    return [
        ApprovalEventResponse(
            **{c: getattr(ev, c) for c in ApprovalEventResponse.model_fields if c != "actor_name"},
            actor_name=full_name,
        )
        for ev, full_name in result.all()
    ]
