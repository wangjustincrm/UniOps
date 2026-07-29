"""CRUD for Invoice with 3-way match logic."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.invoice_tax_line import InvoiceTaxLine
from app.models.po import PoLineItem, PurchaseOrder
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.invoice import (
    AllocationInput,
    InvoiceCreate,
    InvoiceExceptionRequest,
    InvoiceMatchRequest,
    InvoiceUpdate,
)


# ── Number generation ──────────────────────────────────────────────────────────

def within_tolerance(variance: Decimal, variance_pct: Decimal | None, tolerance_pct: Decimal) -> bool:
    """FIN-AP-001 rule: exact match always passes; non-zero variance passes only
    when a positive tolerance is configured and |variance_pct| stays within it."""
    if variance == Decimal("0"):
        return True
    if tolerance_pct <= Decimal("0") or variance_pct is None:
        return False
    return abs(variance_pct) <= tolerance_pct


async def _match_tolerance_pct(db: AsyncSession) -> Decimal:
    from app.models.config import CompanyConfig
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
    value = getattr(cfg, "invoice_match_tolerance_pct", None) if cfg else None
    return value if value is not None else Decimal("0")


async def _next_ref(db: AsyncSession) -> str:
    year = datetime.now(timezone.utc).strftime("%Y")
    prefix = f"INV-{year}-"
    # Highest existing suffix + 1 — NOT count()+1: invoices are hard-deleted, so a
    # delete makes the count fall behind the surviving maximum and the next create
    # collides with the internal_ref unique index. Order by length before value so
    # a five-digit suffix (…-10000) outranks …-9999.
    last = (await db.execute(
        select(Invoice.internal_ref)
        .where(Invoice.internal_ref.like(f"{prefix}%"))
        .order_by(func.length(Invoice.internal_ref).desc(), Invoice.internal_ref.desc())
        .limit(1)
    )).scalar_one_or_none()
    n = int(last[len(prefix):]) if last else 0
    return f"{prefix}{n + 1:04d}"


# ── Reads ──────────────────────────────────────────────────────────────────────

async def get_all(
    db: AsyncSession,
    *,
    status: str | None = None,
    vendor_id: uuid.UUID | None = None,
    po_id: uuid.UUID | None = None,
    search: str | None = None,
    po_ids_subq=None,
    own_uploads_user_id: uuid.UUID | None = None,
    task_user_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Invoice], int]:
    from sqlalchemy import or_
    q = select(Invoice)

    if po_ids_subq is not None or own_uploads_user_id is not None or task_user_id is not None:
        scope_conds = []
        if po_ids_subq is not None:
            alloc_scope = select(InvoicePoAllocation.invoice_id).where(
                InvoicePoAllocation.po_id.in_(po_ids_subq)
            )
            scope_conds.append(Invoice.po_id.in_(po_ids_subq))
            scope_conds.append(Invoice.id.in_(alloc_scope))
        if own_uploads_user_id is not None:
            scope_conds.append(Invoice.uploaded_by == own_uploads_user_id)
        if task_user_id is not None:
            from app.core.access_scope import _open_task_doc_ids
            scope_conds.append(Invoice.id.in_(_open_task_doc_ids(task_user_id, "invoice")))
            # Matcher retention: invoices this user matched remain visible in the list
            scope_conds.append(Invoice.matched_by == task_user_id)
        q = q.where(or_(*scope_conds))

    if status:
        q = q.where(Invoice.status == status)
    if vendor_id:
        q = q.where(Invoice.vendor_id == vendor_id)
    if po_id:
        alloc_by_po = select(InvoicePoAllocation.invoice_id).where(
            InvoicePoAllocation.po_id == po_id
        )
        q = q.where(or_(Invoice.po_id == po_id, Invoice.id.in_(alloc_by_po)))
    if search and search.strip():
        term = f"%{search.strip()}%"
        q = q.where(or_(
            Invoice.internal_ref.ilike(term),
            Invoice.vendor_name.ilike(term),
            Invoice.vendor_invoice_number.ilike(term),
            Invoice.po_number.ilike(term),
        ))
    total: int = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    offset = (page - 1) * page_size
    items = list((await db.execute(
        q.order_by(Invoice.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all())
    return items, total


async def get_by_id(db: AsyncSession, invoice_id: uuid.UUID) -> Invoice | None:
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    inv = result.scalar_one_or_none()
    if inv is None:
        return None
    # Enrich allocations with human-readable PO number + PO line description
    # (display only — resolved at read time, not stored on the allocation row).
    allocs = inv.allocations
    if allocs:
        po_ids = {a.po_id for a in allocs}
        line_ids = {a.po_line_id for a in allocs if a.po_line_id is not None}
        po_numbers = dict((await db.execute(
            select(PurchaseOrder.id, PurchaseOrder.number).where(PurchaseOrder.id.in_(po_ids))
        )).all())
        line_descs: dict = {}
        if line_ids:
            line_descs = dict((await db.execute(
                select(PoLineItem.id, PoLineItem.description).where(PoLineItem.id.in_(line_ids))
            )).all())
        for a in allocs:
            a.po_number = po_numbers.get(a.po_id)
            a.po_line_description = line_descs.get(a.po_line_id) if a.po_line_id else None
    return inv


async def is_visible(db: AsyncSession, invoice: Invoice, scope: dict) -> bool:
    """Return True if this invoice falls within the user's access scope."""
    from sqlalchemy import or_
    po_ids_subq = scope["po_subq"]
    user_id = scope["user_id"]
    role = scope["role"]

    conds = []
    if po_ids_subq is not None:
        alloc_scope = select(InvoicePoAllocation.invoice_id).where(
            InvoicePoAllocation.po_id.in_(po_ids_subq)
        )
        conds.append(Invoice.po_id.in_(po_ids_subq))
        conds.append(Invoice.id.in_(alloc_scope))
    if role == "requester":
        conds.append(Invoice.uploaded_by == user_id)
    # Task-based visibility: if the user has an open match_invoice task for this
    # invoice, they can see it regardless of department/PO scope.
    from app.core.access_scope import _open_task_doc_ids
    conds.append(Invoice.id.in_(_open_task_doc_ids(user_id, "invoice")))
    # Matcher retention: once a user has matched an invoice they retain visibility
    # even after their task is completed (you can see what you acted on).
    conds.append(Invoice.matched_by == user_id)

    if not conds:
        return True  # unrestricted

    result = await db.execute(
        select(Invoice.id).where(Invoice.id == invoice.id).where(or_(*conds))
    )
    return result.scalar_one_or_none() is not None


# ── Create ─────────────────────────────────────────────────────────────────────

async def create(
    db: AsyncSession,
    payload: InvoiceCreate,
    vendor_name: str,
    uploaded_by: uuid.UUID,
) -> Invoice:
    ref = await _next_ref(db)
    total_amount = payload.amount + payload.tax_amount

    # Pre-link PO number if po_id given
    po_number: str | None = None
    if payload.po_id:
        result = await db.execute(
            select(PurchaseOrder.number).where(PurchaseOrder.id == payload.po_id)
        )
        po_number = result.scalar_one_or_none()

    uploader_result = await db.execute(select(User.full_name).where(User.id == uploaded_by))
    uploaded_by_name = uploader_result.scalar_one_or_none()

    invoice = Invoice(
        internal_ref=ref,
        vendor_invoice_number=payload.vendor_invoice_number,
        vendor_id=payload.vendor_id,
        vendor_name=vendor_name,
        amount=payload.amount,
        tax_amount=payload.tax_amount,
        total_amount=total_amount,
        currency=payload.currency,
        invoice_date=payload.invoice_date,
        due_date=payload.due_date,
        line_items=[item.model_dump(mode="json") for item in payload.line_items],
        file_name=payload.file_name,
        file_size=payload.file_size,
        notes=payload.notes,
        uploaded_by=uploaded_by,
        uploaded_by_name=uploaded_by_name,
        po_id=payload.po_id,
        po_number=po_number,
    )
    db.add(invoice)
    await db.flush()
    await db.refresh(invoice)
    return invoice


# ── 3-way match ────────────────────────────────────────────────────────────────

class AllocationImbalance(ValueError):
    """Raised when allocations don't sum to the invoice total (→ HTTP 422)."""


class LegacyMatchUnsupported(ValueError):
    """Raised when a legacy match request can't be expressed as one header-level
    allocation (→ HTTP 422)."""


class FeeOnlyLinkRequired(ValueError):
    """Raised when a fee-only invoice (no PO allocations) is confirmed without a
    reference PO to link it to (→ HTTP 422)."""


async def _normalize_allocations(invoice: Invoice, req: InvoiceMatchRequest) -> list[AllocationInput]:
    """Return the effective allocation list. Legacy single-PO requests become one
    PO-header-level allocation covering the full invoice total."""
    if req.allocations is not None:
        return req.allocations
    if req.po_id is None:
        raise ValueError("Either allocations or po_id is required")
    if req.po_line_ids and len(req.po_line_ids) > 1:
        # The legacy shim can only reference ONE PO line — earlier versions silently
        # dropped the rest and compared the full invoice against the first line.
        raise LegacyMatchUnsupported(
            "Multiple PO lines require line-level allocations — send the 'allocations' field instead of po_line_ids"
        )
    line_id = None
    if invoice.line_items:
        line_id = invoice.line_items[0].get("id")
    if line_id is None:
        line_id = str(uuid.uuid4())
    po_line_id = req.po_line_ids[0] if req.po_line_ids else None
    return [AllocationInput(
        invoice_line_id=uuid.UUID(str(line_id)),
        po_id=req.po_id,
        po_line_id=po_line_id,
        allocated_amount=invoice.amount,
        allocated_tax=Decimal("0"),
    )]


async def match(
    db: AsyncSession,
    invoice: Invoice,
    req: InvoiceMatchRequest,
    matched_by: uuid.UUID,
    require_review: bool = False,
) -> Invoice:
    now = datetime.now(timezone.utc)
    allocs = await _normalize_allocations(invoice, req)

    # 0. 非PO费用行:排除出 PO 匹配,但其税前 line_total 计入平账(照付,随发票头
    # 走 AP)。每次 match 以入参为准重写标记 —— 未列出的行清除标记(支持 unmark)。
    non_po_map: dict[str, str | None] = {
        str(n.line_id): n.note for n in (req.non_po_lines or [])
    }
    excluded_total = Decimal("0")
    if invoice.line_items:
        for li in invoice.line_items:
            lid = str(li.get("id"))
            if lid in non_po_map:
                li["non_po_fee"] = True
                li["non_po_note"] = non_po_map[lid]
                excluded_total += Decimal(str(li.get("line_total") or "0"))
            else:
                li["non_po_fee"] = False
                li["non_po_note"] = None
        flag_modified(invoice, "line_items")

    # 0b. mutual exclusion: a line cannot be both allocated to a PO and marked
    # non-PO fee — that would double-count its amount in the balance check
    # below (real allocation + excluded_total) and could mask an imbalance.
    overlap = {str(a.invoice_line_id) for a in allocs} & set(non_po_map)
    if overlap:
        raise AllocationImbalance(
            f"Line(s) {sorted(overlap)} cannot be both allocated to a PO and marked as non-PO fee"
        )

    # 1. integrity: allocations carry PRE-TAX amounts (invoice/PO lines are
    # pre-tax; tax reconciles at the invoice header), so pre-tax allocations
    # PLUS non-PO fee lines must equal the invoice's pre-tax amount.
    alloc_total = sum((a.allocated_amount for a in allocs), Decimal("0"))
    if abs(alloc_total + excluded_total - invoice.amount) > Decimal("0.01"):
        raise AllocationImbalance(
            f"Allocations {alloc_total} + non-PO fees {excluded_total} must equal "
            f"invoice pre-tax amount {invoice.amount}"
        )

    # 2. validate referenced POs/lines, cache PO objects
    po_cache: dict[uuid.UUID, PurchaseOrder] = {}
    for a in allocs:
        if a.po_id not in po_cache:
            po = (await db.execute(
                select(PurchaseOrder).where(PurchaseOrder.id == a.po_id)
            )).scalar_one_or_none()
            if po is None:
                raise ValueError(f"Purchase order {a.po_id} not found")
            po_cache[a.po_id] = po
        if a.po_line_id is not None:
            exists = (await db.execute(
                select(PoLineItem.id)
                .where(PoLineItem.id == a.po_line_id, PoLineItem.po_id == a.po_id)
            )).scalar_one_or_none()
            if exists is None:
                raise ValueError(f"PO line {a.po_line_id} not on PO {a.po_id}")

    # 3. rebuild allocations (idempotent)
    await db.execute(sa_delete(InvoicePoAllocation).where(InvoicePoAllocation.invoice_id == invoice.id))

    tolerance = await _match_tolerance_pct(db)
    any_exception = False
    new_rows: list[InvoicePoAllocation] = []
    for a in allocs:
        allocated_total = a.allocated_amount + a.allocated_tax
        row = InvoicePoAllocation(
            invoice_id=invoice.id,
            invoice_line_id=a.invoice_line_id,
            po_id=a.po_id,
            po_line_id=a.po_line_id,
            allocated_amount=a.allocated_amount,
            allocated_tax=a.allocated_tax,
            allocated_total=allocated_total,
            note=a.note,
        )
        db.add(row)
        new_rows.append(row)
    await db.flush()

    # 4. per (po_id, po_line_id) variance vs reference, across ALL invoices
    for row in new_rows:
        po = po_cache[row.po_id]
        if row.po_line_id is not None:
            reference = (await db.execute(
                select(PoLineItem.line_total).where(PoLineItem.id == row.po_line_id)
            )).scalar_one()
        else:
            reference = po.subtotal   # pre-tax PO total (header-level fallback)

        # Reference (PoLineItem.line_total / po.subtotal) is PRE-TAX, so the
        # invoiced side must be pre-tax too — sum allocated_amount (pre-tax),
        # NOT allocated_total (tax-inclusive). Mirrors the header integrity
        # check above which also compares pre-tax allocated_amount.
        q = select(func.coalesce(func.sum(InvoicePoAllocation.allocated_amount), Decimal("0"))) \
            .where(InvoicePoAllocation.po_id == row.po_id)
        if row.po_line_id is not None:
            q = q.where(InvoicePoAllocation.po_line_id == row.po_line_id)
        else:
            q = q.where(InvoicePoAllocation.po_line_id.is_(None))
        invoiced = (await db.execute(q)).scalar_one() or Decimal("0")

        variance = invoiced - reference
        variance_pct = (
            (variance / reference * 100).quantize(Decimal("0.0001"))
            if reference != Decimal("0") else Decimal("0")
        )
        row.variance = variance
        row.variance_pct = variance_pct
        # 部分开票规则(2026-07-10):少开(variance<0)放行 —— PO line 可由多张
        # 发票分次开票;只有累计【超开】超容差才算 exception。
        if variance > Decimal("0") and not within_tolerance(variance, variance_pct, tolerance):
            any_exception = True

    # 5. roll up to invoice header (summary + backward-compat single values)
    invoice.matched_at = now
    invoice.matched_by = matched_by
    invoice.matched_by_name = (await db.execute(
        select(User.full_name).where(User.id == matched_by)
    )).scalar_one_or_none()

    if allocs:
        first = allocs[0]
        primary_po = po_cache[first.po_id]
        invoice.po_id = primary_po.id
        invoice.po_number = primary_po.number

        seen: set[tuple] = set()
        summary_reference = Decimal("0")
        for row in new_rows:
            key = (row.po_id, row.po_line_id)
            if key in seen:
                continue
            seen.add(key)
            if row.po_line_id is not None:
                summary_reference += (await db.execute(
                    select(PoLineItem.line_total).where(PoLineItem.id == row.po_line_id)
                )).scalar_one()
            else:
                summary_reference += po_cache[row.po_id].subtotal
        invoice.po_total = summary_reference
        # Header variance measures only the PO-matched portion of the invoice: a
        # non-PO fee line's pre-tax amount is not part of the PO reference, so it
        # must be subtracted from the invoice side here or a perfectly-balanced
        # mixed invoice (PO alloc + non-PO fee) would show a spurious variance.
        invoice.variance = (invoice.amount - excluded_total) - summary_reference
        invoice.variance_pct = (
            (invoice.variance / summary_reference * 100).quantize(Decimal("0.0001"))
            if summary_reference != Decimal("0") else Decimal("0")
        )
    else:
        # Fee-only invoice: no PO allocations. Link it to a reference PO for
        # traceability; the fees are paid in full via the AP header. There is no
        # PO line reference to measure variance against, so variance is zero.
        if req.reference_po_id is None:
            raise FeeOnlyLinkRequired("Link a PO to confirm a fee-only invoice")
        ref_po = (await db.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == req.reference_po_id)
        )).scalar_one_or_none()
        if ref_po is None:
            raise ValueError(f"Purchase order {req.reference_po_id} not found")
        invoice.po_id = ref_po.id
        invoice.po_number = ref_po.number
        invoice.po_total = Decimal("0")
        invoice.variance = Decimal("0")
        invoice.variance_pct = Decimal("0")

    invoice.matched_po_line_ids = None
    invoice.matched_reference_total = None

    # GR handling unchanged (whole-invoice level)
    effective_gr_ids: list[uuid.UUID] = []
    if req.gr_ids:
        effective_gr_ids = list(req.gr_ids)
    elif req.gr_id:
        effective_gr_ids = [req.gr_id]
    if effective_gr_ids:
        gr_value_total = Decimal("0")
        gr_numbers: list[str] = []
        first_gr_id: uuid.UUID | None = None
        for gid in effective_gr_ids:
            gr_obj = (await db.execute(select(GoodsReceipt).where(GoodsReceipt.id == gid))).scalar_one_or_none()
            if gr_obj:
                if first_gr_id is None:
                    first_gr_id = gr_obj.id
                gr_numbers.append(gr_obj.number)
                gr_value_total += sum((it.line_total for it in gr_obj.line_items), Decimal("0"))
        invoice.gr_id = first_gr_id
        invoice.gr_number = ", ".join(gr_numbers) if gr_numbers else None
        invoice.gr_value = gr_value_total
        invoice.gr_ids = [str(gid) for gid in effective_gr_ids]
    else:
        invoice.gr_id = None
        invoice.gr_number = None
        invoice.gr_value = None
        invoice.gr_ids = None

    all_zero = all((row.variance or Decimal("0")) == Decimal("0") for row in new_rows)
    if require_review and not all_zero:
        invoice.status = "match_review"
        invoice.exception_reason = None
    elif any_exception:
        invoice.status = "exception"
        invoice.exception_reason = (
            "One or more PO lines are outside tolerance "
            f"(invoice total {invoice.total_amount} vs reference {summary_reference})"
        )
    else:
        invoice.status = "matched"
        if invoice.variance is not None and invoice.variance < Decimal("0"):
            invoice.exception_reason = (
                f"Partially invoiced against PO reference (variance: {invoice.variance:+.2f}) — "
                "remaining amount may be billed by later invoices"
            )
        elif invoice.variance != Decimal("0"):
            invoice.exception_reason = (
                f"Auto-matched within tolerance {tolerance}% (variance: {invoice.variance:+.2f})"
            )

    await db.flush()
    await db.refresh(invoice)
    return invoice


async def review_match(
    db: AsyncSession,
    invoice: Invoice,
    action: str,
    note: str | None,
    reviewer_id: uuid.UUID,
) -> Invoice:
    """复核被指派人的 match:approve 按容差落定,reject 回 unmatched。"""
    if invoice.status != "match_review":
        raise ValueError(f"Invoice is not pending review (status '{invoice.status}')")
    if action == "approve":
        rows = (await db.execute(
            select(InvoicePoAllocation).where(InvoicePoAllocation.invoice_id == invoice.id)
        )).scalars().all()
        tolerance = await _match_tolerance_pct(db)
        any_exception = any(
            (r.variance or Decimal("0")) > Decimal("0")
            and not within_tolerance(r.variance or Decimal("0"), r.variance_pct or Decimal("0"), tolerance)
            for r in rows
        )
        if any_exception:
            invoice.status = "exception"
            invoice.exception_reason = (
                "Reviewed: variance outside tolerance "
                f"(invoice total {invoice.total_amount} vs reference {invoice.po_total})"
            )
        else:
            invoice.status = "matched"
            invoice.exception_reason = (
                f"Reviewed and approved (variance: {invoice.variance:+.2f})"
                if invoice.variance else None
            )
    else:  # reject
        invoice.status = "unmatched"
        invoice.matched_at = None
        invoice.matched_by = None
        invoice.matched_by_name = None
        invoice.exception_reason = None   # defensive: stale reason must not survive back to unmatched
        # 分摊行保留供参考;下次 match 会整体重建(match() 幂等删除)
    await db.flush()
    await db.refresh(invoice)
    return invoice


async def rematch_from_existing(db: AsyncSession, invoice: Invoice, matched_by: uuid.UUID) -> Invoice:
    """Re-run match using the invoice's CURRENT allocations. If they no longer sum
    to the (possibly edited) total, clear them and reset to unmatched."""
    rows = (await db.execute(
        select(InvoicePoAllocation).where(InvoicePoAllocation.invoice_id == invoice.id)
    )).scalars().all()
    if not rows:
        return invoice
    # Allocations are PRE-TAX (tax stays at the invoice header), so balance them
    # against the pre-tax amount — mirroring match()'s integrity check. Comparing
    # against total_amount here wrongly reset taxed invoices to unmatched on edit.
    alloc_total = sum((r.allocated_amount for r in rows), Decimal("0"))
    if abs(alloc_total - invoice.amount) > Decimal("0.01"):
        former_po_id = invoice.po_id
        await db.execute(sa_delete(InvoicePoAllocation).where(InvoicePoAllocation.invoice_id == invoice.id))
        invoice.status = "unmatched"
        invoice.po_id = None
        invoice.po_number = None
        invoice.po_total = None
        invoice.variance = None
        invoice.variance_pct = None
        invoice.matched_at = None
        await db.flush()
        # This invoice no longer backs a payment; clear the PO's create_pa task
        # if nothing else matched to it (else it lingers as an orphan forever).
        if former_po_id is not None:
            from app.crud.task import _complete_orphan_create_pa_tasks
            await _complete_orphan_create_pa_tasks(db, former_po_id)
        await db.refresh(invoice, ["allocations"])
        return invoice
    req = InvoiceMatchRequest(allocations=[
        AllocationInput(
            invoice_line_id=r.invoice_line_id, po_id=r.po_id, po_line_id=r.po_line_id,
            allocated_amount=r.allocated_amount, allocated_tax=r.allocated_tax, note=r.note,
        ) for r in rows
    ], gr_ids=[uuid.UUID(g) for g in invoice.gr_ids] if invoice.gr_ids else None)
    return await match(db, invoice, req, matched_by)


# ── Update ─────────────────────────────────────────────────────────────────────

async def update(db: AsyncSession, invoice: Invoice, payload: InvoiceUpdate) -> Invoice:
    if payload.vendor_invoice_number is not None:
        invoice.vendor_invoice_number = payload.vendor_invoice_number
    if payload.invoice_date is not None:
        invoice.invoice_date = payload.invoice_date
    if payload.due_date is not None:
        invoice.due_date = payload.due_date
    if payload.currency is not None:
        invoice.currency = payload.currency
    if payload.amount is not None:
        invoice.amount = payload.amount
    # Tax header source of truth: when the invoice carries invoice_tax_lines, the
    # header tax_amount MUST equal their sum (mirrors invoice_tax.py:100-102), so
    # an edited payload.tax_amount is ignored to avoid desyncing header ↔ lines.
    # Only when there are NO tax lines does payload.tax_amount apply.
    tax_rows = (await db.execute(
        select(InvoiceTaxLine.tax_amount).where(InvoiceTaxLine.invoice_id == invoice.id)
    )).scalars().all()
    if tax_rows:
        invoice.tax_amount = sum(tax_rows, Decimal("0"))
    elif payload.tax_amount is not None:
        invoice.tax_amount = payload.tax_amount
    if payload.notes is not None:
        invoice.notes = payload.notes
    if payload.line_items is not None:
        invoice.line_items = [item.model_dump(mode="json") for item in payload.line_items]
    # GR selection from the edit form (only sent when a PO is linked). Persist it
    # here so the subsequent rematch_from_existing → match() re-derives gr_id /
    # gr_number / gr_value. [] means "clear all GRs".
    if payload.gr_ids is not None:
        invoice.gr_ids = [str(g) for g in payload.gr_ids]
    invoice.total_amount = invoice.amount + invoice.tax_amount
    await db.flush()
    await db.refresh(invoice)
    return invoice


# ── Exception resolution ───────────────────────────────────────────────────────

async def resolve_exception(
    db: AsyncSession,
    invoice: Invoice,
    req: InvoiceExceptionRequest,
    resolved_by: uuid.UUID,
) -> Invoice:
    if invoice.status != "exception":
        raise ValueError(f"Invoice is not in exception status (current: {invoice.status})")
    now = datetime.now(timezone.utc)
    resolver_result = await db.execute(select(User.full_name).where(User.id == resolved_by))
    invoice.exception_resolved_at = now
    invoice.exception_resolved_by = resolved_by
    invoice.exception_resolved_by_name = resolver_result.scalar_one_or_none()
    invoice.exception_resolution = req.resolution
    if req.note:
        invoice.exception_reason = req.note
    # After resolution, move to matched (accepted) or stay as-is
    if req.resolution == "accepted":
        invoice.status = "matched"
    await db.flush()
    await db.refresh(invoice)
    return invoice


# ── Delete ─────────────────────────────────────────────────────────────────────

async def delete(db: AsyncSession, invoice: Invoice) -> None:
    if invoice.status not in ("unmatched", "exception"):
        raise ValueError(
            f"Cannot delete invoice in '{invoice.status}' status. "
            "Only unmatched or exception invoices may be deleted."
        )
    po_id = invoice.po_id
    await db.delete(invoice)
    await db.flush()
    # If this was the last invoice keeping a create_pa task alive for its PO,
    # complete that task so it doesn't linger in the requester's inbox.
    if po_id is not None:
        from app.crud.task import _complete_orphan_create_pa_tasks
        await _complete_orphan_create_pa_tasks(db, po_id)


# ── Status update ──────────────────────────────────────────────────────────────

async def update_status(db: AsyncSession, invoice: Invoice, status: str) -> Invoice:
    invoice.status = status
    await db.flush()
    await db.refresh(invoice)
    return invoice
