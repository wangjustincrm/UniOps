"""CRUD for Invoice with 3-way match logic."""
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.crud import agreement as agreement_crud
from app.crud import agreement_receipt as agreement_receipt_crud
from app.crud import agreement_schedule as agreement_schedule_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_receipt import AgreementReceipt
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.invoice_tax_line import InvoiceTaxLine
from app.models.pa import PaymentApplication
from app.models.po import PoLineItem, PurchaseOrder
from app.models.task import Task
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.invoice import (
    AllocationInput,
    InvoiceCreate,
    InvoiceExceptionRequest,
    InvoiceMatchRequest,
    InvoiceUpdate,
)

logger = logging.getLogger(__name__)


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
    agreement_id: uuid.UUID | None = None,
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
    if agreement_id:
        q = q.where(Invoice.agreement_id == agreement_id)
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


class AgreementMatchInvalid(Exception):
    """The invoice cannot be matched to the requested agreement (→ HTTP 422)."""


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


async def _apply_gr_selection(
    db: AsyncSession, invoice: Invoice, gr_ids: list[uuid.UUID] | None,
) -> None:
    """Derive the scalar gr_id / gr_number / gr_value (and the normalized gr_ids)
    on the invoice from a GR selection. Shared by match() and update() so a GR
    selection lands consistently even for fee-only / reference-only invoices,
    which carry no InvoicePoAllocation rows and therefore never flow through
    rematch_from_existing → match() (which returns early when there are no
    allocations, leaving gr_id NULL and breaking the 3-Way Match view + the PA
    receipt gate). An empty list clears the selection. Idempotent — sets absolute
    values, never appends, so a later match() recomputing the same GRs is a no-op."""
    effective = list(gr_ids or [])
    if not effective:
        invoice.gr_id = None
        invoice.gr_number = None
        invoice.gr_value = None
        invoice.gr_ids = None
        return
    gr_value_total = Decimal("0")
    gr_numbers: list[str] = []
    first_gr_id: uuid.UUID | None = None
    for gid in effective:
        gr_obj = (await db.execute(select(GoodsReceipt).where(GoodsReceipt.id == gid))).scalar_one_or_none()
        if gr_obj:
            if first_gr_id is None:
                first_gr_id = gr_obj.id
            gr_numbers.append(gr_obj.number)
            gr_value_total += sum((it.line_total for it in gr_obj.line_items), Decimal("0"))
    invoice.gr_id = first_gr_id
    invoice.gr_number = ", ".join(gr_numbers) if gr_numbers else None
    invoice.gr_value = gr_value_total
    invoice.gr_ids = [str(gid) for gid in effective]


# GRs in these states never represent received goods, so they must never be
# auto-attached to an invoice (a user can still pick one by hand).
_DEAD_GR_STATUSES = ("cancelled", "rejected")


async def _discover_grs_for_allocations(
    db: AsyncSession, allocs: list[AllocationInput],
) -> list[uuid.UUID]:
    """GRs that already received what these allocations bill — the mirror image of
    gr.crud._autofill_gr_to_matched_invoices, for the "received first, invoiced
    later" order that the GR-create hook cannot cover.

    Line-level allocations route by po_line_id; header-level ones (total-value
    match / legacy shim) carry no line, so they fall back to every live GR on the
    PO. Oldest first, so gr_id (the scalar) lands on the earliest receipt.
    """
    line_ids = [a.po_line_id for a in allocs if a.po_line_id is not None]
    header_po_ids = [a.po_id for a in allocs if a.po_line_id is None]
    found: list[uuid.UUID] = []
    if line_ids:
        receiving_grs = select(GrLineItem.gr_id).where(GrLineItem.po_line_id.in_(line_ids))
        found += (await db.execute(
            select(GoodsReceipt.id)
            .where(GoodsReceipt.id.in_(receiving_grs),
                   GoodsReceipt.status.not_in(_DEAD_GR_STATUSES))
            .order_by(GoodsReceipt.created_at)
        )).scalars().all()
    if header_po_ids:
        found += (await db.execute(
            select(GoodsReceipt.id)
            .where(GoodsReceipt.po_id.in_(header_po_ids),
                   GoodsReceipt.status.not_in(_DEAD_GR_STATUSES))
            .order_by(GoodsReceipt.created_at)
        )).scalars().all()
    seen: set[uuid.UUID] = set()
    return [g for g in found if not (g in seen or seen.add(g))]


async def _recompute_consumed(db: AsyncSession, agreement_id: uuid.UUID) -> None:
    """consumed_amount is derived, never incremented.

    Deriving it from the invoice set makes re-match idempotent: incrementing
    would double-count every correction, and the number drives the NTE warning
    banner AP looks at.
    """
    total = (await db.execute(
        select(func.coalesce(func.sum(Invoice.total_amount), Decimal("0")))
        .where(Invoice.agreement_id == agreement_id)
    )).scalar_one()
    agr = (await db.execute(
        select(PurchaseAgreement).where(PurchaseAgreement.id == agreement_id)
    )).scalar_one()
    agr.consumed_amount = total


async def _match_to_agreement(
    db: AsyncSession, invoice: Invoice, req: InvoiceMatchRequest, matched_by: uuid.UUID,
    require_review: bool = False,
) -> Invoice:
    agr = (await db.execute(
        select(PurchaseAgreement).where(PurchaseAgreement.id == req.agreement_id)
    )).scalar_one_or_none()
    if agr is None:
        raise ValueError(f"Agreement {req.agreement_id} not found")
    if agr.vendor_id != invoice.vendor_id:
        raise AgreementMatchInvalid(
            "Agreement must belong to the same vendor as the invoice")

    # One rule, one implementation. This used to be a Python rewrite of
    # crud/agreement.py::_admissible_predicate ("same predicate as an interval
    # comparison instead of an integer day difference"). The two agreed only
    # because the rule was inert — status "active" short-circuited before any
    # date was read. Now that the validity window is load-bearing (see
    # _admissible_predicate) their date sources genuinely differ: SQL
    # `literal(today)` evaluated server-side vs a process-local date.today().
    # Call the predicate instead of restating it.
    if not await agreement_crud.is_admissible(db, agr):
        raise AgreementMatchInvalid(
            f"Agreement {agr.number} is not open for new spend "
            f"(status={agr.status}, valid to {agr.valid_to} + {agr.grace_days or 0}d grace); "
            "renew it before matching invoices to it.")

    # Review fix (Important #1, Task 5 round 1): release any evidence this
    # invoice is CURRENTLY holding — a claimed schedule row and/or claimed
    # agreement receipts — before the type branch below claims new evidence
    # (or falls back to the no-evidence reason). Without this, a rematch
    # through this function (e.g. Data Maintenance resets invoice.status back
    # to "unmatched" with no release hook, then the invoice is POSTed to
    # /match again) silently overwrites invoice.receipt_ids /
    # invoice.schedule_id — the previously-claimed rows stay
    # "reconciled"/"received" with invoice_id still pointing at this invoice
    # forever, and since _release_agreement_evidence only ever discovers rows
    # via invoice.receipt_ids/.schedule_id, they become permanently
    # unreachable (update()/void() both refuse a reconciled receipt; there is
    # no UI path back to open). This also covers switching agreement TYPE
    # (house_account holding receipts → recurring/milestone claiming a
    # schedule row): the old code never touched receipt_ids in that branch at
    # all, leaving a stale pointer at a receipt that now belongs to nobody's
    # current match.
    # No-op when the invoice holds neither (the normal first-time-match case).
    if invoice.receipt_ids or invoice.schedule_id:
        await _release_agreement_evidence(db, invoice)

    # 1A 曾把**所有**协议匹配都当成"无凭证付款":那时协议匹配确实没有任何凭证。
    # 1B 之后 recurring 有排期行 + 履约确认、milestone 有阶段行,都是真凭证 ——
    # 再统一标 legacy 会把每一张正常的周期账单算进协议详情页的
    # "settled without receipt" 计数里,那个健康度指标就废了。
    claimed_row = None
    if agr.agreement_type == "house_account":
        # Task 6: 匹配 = 只做关联。凭证挂载是发票详情页上的另一件事,无凭证结算的
        # 声明也在那里(Task 7/8) —— 两者都不该卡住"这张票属于这份协议"这个独立
        # 事实。唯一的硬约束在 PA 闸门(api/v1/pa.py):起付款时才要求要么有凭证、
        # 要么有显式声明。这里既不设 legacy_settlement 也不清它 —— 一张已经声明
        # 过无凭证的发票改挂到另一份协议时,那个声明依然成立,与挂在哪份协议
        # 无关(receipt_ids/schedule_id 已在函数开头的释放调用里清空,不在这重复)。
        pass
    else:
        invoice.legacy_settlement = False
        invoice.legacy_settlement_reason = None
        if agr.agreement_type == "recurring":
            # Whole-branch review finding: spec §4.3 step 5's manual-assignment
            # escape hatch was never built — an invoice that FIFO can't claim
            # (out of tolerance / schedule exhausted) used to be permanently
            # stuck: it lands in match_review with schedule_id NULL, approving
            # it there never sets schedule_id (PA then 422s "not linked to a
            # billing period" with no way to resolve), and rejecting it just
            # re-runs the SAME FIFO row against the SAME tolerance. An explicit
            # req.schedule_id here is a human overriding that FIFO/tolerance
            # decision on purpose, so it claims the row directly and skips the
            # amount check entirely.
            if req.schedule_id is not None:
                try:
                    claimed_row = await agreement_schedule_crud.claim_specific_period(
                        db, agr, invoice, req.schedule_id)
                except ValueError as exc:
                    raise AgreementMatchInvalid(str(exc)) from exc
                await agreement_schedule_crud.create_confirm_task(db, agr, claimed_row)
            else:
                claimed_row = await agreement_schedule_crud.claim_next_period(db, agr, invoice)
                if claimed_row is None:
                    # 认不到期次(超容差 / 无候选行)就不猜,停在复核队列由人工指定。
                    require_review = True
                else:
                    # recurring 免 GR —— 履约确认是它唯一的代偿,认领成功就派任务。
                    await agreement_schedule_crud.create_confirm_task(db, agr, claimed_row)
        else:   # milestone
            if req.schedule_id is None:
                raise AgreementMatchInvalid(
                    "Pick the milestone stage this invoice pays for")
            try:
                claimed_row = await agreement_schedule_crud.claim_milestone(
                    db, agr, invoice, req.schedule_id)
            except ValueError as exc:
                raise AgreementMatchInvalid(str(exc)) from exc

    previous_agreement_id = invoice.agreement_id

    # Clear any PO-route state so a re-routed invoice doesn't carry stale links.
    await db.execute(sa_delete(InvoicePoAllocation).where(
        InvoicePoAllocation.invoice_id == invoice.id))
    invoice.po_id = None
    invoice.po_number = None
    invoice.matched_po_line_ids = None
    invoice.matched_reference_total = None
    invoice.gr_ids = None
    invoice.gr_id = None
    invoice.gr_number = None

    invoice.agreement_id = agr.id
    invoice.agreement_number = agr.number
    invoice.match_route = "agreement"
    invoice.schedule_id = claimed_row.id if claimed_row else None
    # An explicit req.schedule_id (the manual-assignment escape hatch above)
    # is a human overriding FIFO/tolerance on purpose — it must not read as
    # "automatic" just because a row got claimed. Milestone is never "auto"
    # either (schedule_id is always caller-picked there), which this
    # expression already captured via the agreement_type=="recurring" guard.
    invoice.match_route_auto = (
        agr.agreement_type == "recurring" and claimed_row is not None
        and req.schedule_id is None
    )
    invoice.po_total = Decimal("0")
    invoice.gr_value = None
    invoice.variance = Decimal("0")
    invoice.variance_pct = Decimal("0")
    invoice.exception_reason = None
    invoice.matched_at = datetime.now(timezone.utc)
    invoice.matched_by = matched_by
    invoice.matched_by_name = (await db.execute(
        select(User.full_name).where(User.id == matched_by)
    )).scalar_one_or_none()
    # Mirrors the PO branch's require_review gate (code review finding,
    # 2026-08-07): the PO branch only enters "match_review" when a delegated
    # (non-AP, non-uploader) caller matched AND the numbers show a non-zero
    # variance — a zero-variance PO match is objectively confirmed against a
    # PO line, so a delegate can complete it terminally without a second set
    # of eyes. The agreement route has no such objective reference at all
    # (no PO line, no GR — that is the entire point of legacy_settlement), so
    # there is never a "this is independently verified" case to exempt: if
    # require_review is set, every agreement match by a delegate goes to
    # review, full stop. Skipping this would leave the LEAST-evidenced route
    # with the WEAKEST control, backwards from what require_review exists to
    # guard against.
    invoice.status = "match_review" if require_review else "matched"

    await db.flush()
    # consumed_amount must reflect BOTH sides of a route change: the newly-linked
    # agreement gains this invoice, and — if the invoice previously pointed at a
    # different agreement — that agreement must shed it, or its consumed_amount
    # stays permanently inflated by an invoice it no longer backs.
    await _recompute_consumed(db, agr.id)
    if previous_agreement_id and previous_agreement_id != agr.id:
        await _recompute_consumed(db, previous_agreement_id)
    await db.commit()
    await db.refresh(invoice)
    return invoice


async def _release_agreement_evidence(db: AsyncSession, invoice: Invoice) -> None:
    """Release every piece of agreement-side evidence an invoice holds — the
    claimed AgreementPaymentSchedule row (single) and any claimed
    AgreementReceipt rows (potentially several) — back to unclaimed, when
    the invoice that claimed them is being detached from its agreement
    (route switch, or a match_review rejection).

    Renamed from _release_schedule_row (Task 4): it now covers both kinds of
    evidence a purchase agreement can substitute for a goods receipt —
    schedule rows for recurring agreements, agreement receipts for house
    accounts — because Phase 1B already learned the hard way (twice) what
    happens when a release path is duplicated instead of shared: one call
    site drifts and silently skips the release. Same helper, same three call
    sites, now wider scope.

    Extracted (code review finding, Task 7 fix round) so the release logic
    lives in exactly one place: originally only match()'s agreement→PO
    cleanup called this, but review_match()'s reject path detaches the same
    agreement link without ever touching schedule_id — leaving the period
    permanently marked "received" against an invoice that no longer backs
    it, so it can never be claimed by a later invoice and silently
    under-reports what's still owed. Both call sites now share this.

    Whole-branch review finding (4a/4b), two more gaps in the same release:

    4a — a release used to leave accepted_by/accepted_at standing on the row.
    A delegate's match claims a period and a manager confirms it; AP then
    rejects the match → the row released back to "pending" but STILL stamped
    confirmed. A later invoice re-claims that same period and sails straight
    past the PA gate (`accepted_at IS NULL`) — paid with nobody having
    confirmed service for ITS billing cycle. The stamp describes the invoice
    being detached, not the period in the abstract, so it must go with it.

    4b — release never completed the open confirm_period task either, so a
    later re-claim's create_confirm_task adds a SECOND task with the same
    document_number. agreements.py's confirm endpoint used to look that task
    up with `.scalar_one_or_none()`, which raises MultipleResultsFound (a 500)
    the instant that happens — and the period becomes permanently
    unconfirmable. (crud.confirm_period already tolerates duplicates via
    `.scalars().all()`; the two layers now agree.)
    """
    if invoice.schedule_id is not None:
        claimed_row = (await db.execute(
            select(AgreementPaymentSchedule).where(
                AgreementPaymentSchedule.id == invoice.schedule_id)
        )).scalar_one_or_none()
        if claimed_row is not None:
            # Deferred minor from Task 6, folded in here: only release a row this
            # SAME invoice actually holds. invoice.schedule_id should always point
            # back at a row whose invoice_id mirrors it (both are only ever set
            # together, by claim_next_period / claim_specific_period /
            # claim_milestone) — but if that invariant were ever broken by a bug
            # elsewhere, blindly releasing here would silently steal a period a
            # DIFFERENT invoice is legitimately holding.
            if claimed_row.invoice_id != invoice.id:
                logger.error(
                    "_release_agreement_evidence: schedule row %s is claimed by "
                    "invoice %s, not %s (invoice.schedule_id pointed at it "
                    "anyway) — leaving the row untouched, only clearing "
                    "invoice.schedule_id",
                    claimed_row.id, claimed_row.invoice_id, invoice.id,
                )
            else:
                claimed_row.status = "pending"
                claimed_row.invoice_id = None
                claimed_row.accepted_by = None
                claimed_row.accepted_at = None
                if claimed_row.period_label is not None:
                    agr_number = (await db.execute(
                        select(PurchaseAgreement.number).where(
                            PurchaseAgreement.id == claimed_row.agreement_id)
                    )).scalar_one_or_none()
                    if agr_number is not None:
                        open_tasks = (await db.execute(
                            select(Task).where(
                                Task.document_type == "agr",
                                Task.document_id == claimed_row.agreement_id,
                                Task.type == "confirm_period",
                                Task.document_number == f"{agr_number} · {claimed_row.period_label}",
                                Task.is_completed.is_(False),
                            )
                        )).scalars().all()
                        now = datetime.now(timezone.utc)
                        for t in open_tasks:
                            t.is_completed = True
                            t.completed_at = now
        invoice.schedule_id = None

    # 凭证释放 —— 与排期行同理,但凭证是**多张**:invoice.receipt_ids 是数组。
    # 逐张放回 open 并清 invoice_id;只动确实由这张发票持有的行(防止把别的
    # 发票刚认领的同一张凭证抢回来 —— 当前不可达,但 Task 6 的匹配分支会写
    # 这个字段,不变量要自己成立,不能依赖调用方)。
    receipt_ids = invoice.receipt_ids or []
    if receipt_ids:
        rows = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id.in_(receipt_ids))
        )).scalars().all()
        for row in rows:
            if row.invoice_id != invoice.id:
                logger.warning(
                    "_release_agreement_evidence: receipt %s is claimed by invoice %s, "
                    "not %s — leaving it alone", row.id, row.invoice_id, invoice.id)
                continue
            row.status = "open"
            row.invoice_id = None
    invoice.receipt_ids = None
    invoice.receipt_variance_reason = None
    await db.flush()


async def _invoice_referenced_by_active_pa(db: AsyncSession, invoice_id: uuid.UUID) -> bool:
    """True when a non-cancelled Payment Application already lists this
    invoice in its invoice_ids JSONB array.

    Fix-round 1 review finding (Important #1): set_receipts /
    settle_without_receipt had NO status gate at all — an invoice's uploader
    (always passes _require_invoice_match_access, see is_uploader there)
    could PUT an empty receipt list onto an invoice a PA has already been
    raised — even PAID — against. That would release its claimed receipts
    back to `open` for a DIFFERENT invoice to claim (the same paper receipt
    backing two payments) or plant an irreversible legacy_settlement=True
    flag on an invoice that already cleared payment. Nothing else in the
    request path catches this: an invoice's own `status` stays "matched"
    forever after a PA pays it (_mark_invoices_paid in crud/pa.py flips it to
    "paid" only through `_mark_invoices_paid`'s own success path, and even
    that doesn't stop THIS function from being called afterward), and the
    house_account evidence gate in api/v1/pa.py's
    `_validate_agreement_pa_invoices` only runs when a PA is CREATED or its
    invoice_ids are PATCHed — never continuously, so it cannot itself catch
    evidence being pulled out from under a PA that already exists.

    "Referenced" is checked via JSONB containment (`invoice_ids @>
    [str(invoice_id)]`) rather than joining through Invoice, because
    PaymentApplication.invoice_ids has no FK back to invoices (shared table,
    OA/finance also write it) — the JSONB array is the only link there is.
    `status != "cancelled"` matches this codebase's convention for "PA is
    still live" (a cancelled PA no longer holds a real claim on the invoice's
    evidence).
    """
    row = (await db.execute(
        select(PaymentApplication.id)
        .where(PaymentApplication.invoice_ids.contains([str(invoice_id)]),
               PaymentApplication.status != "cancelled")
        .limit(1)
    )).first()
    return row is not None


async def set_receipts(
    db: AsyncSession, invoice: Invoice, receipt_ids: list[uuid.UUID],
    variance_reason: str | None,
) -> Invoice:
    """全量覆盖一张发票持有的凭证集合(Task 7:挂凭证是发票详情页上独立于
    /match 的一个动作 —— 见 InvoiceMatchRequest 上那段关于 Task 6 拆分的注释)。

    先无条件释放当前持有的全部凭证,再认领入参里的 —— 而不是做增量 diff。
    理由:diff 要同时维护"新增"和"移除"两条路径,而本项目在释放这件事上
    已经被咬过多次(每次都是某一条路径漏了释放,参见 _release_agreement_evidence
    的开发历史)。释放-再认领只有一条路径,多余的写入换来一个不可能漏的
    不变式:调用后 invoice.receipt_ids 永远等于且只等于 receipt_ids 里能通过
    claim() 校验的那些 id(空列表 → 什么都不认领,等价于清空)。

    Fix-round 1 (Important #1): refuses to touch an invoice a non-cancelled
    PA already references — see _invoice_referenced_by_active_pa. Checked
    BEFORE the release call runs, so a rejected request leaves every
    currently-claimed receipt untouched.

    Fix-round 1 (Minor #6): unlike `claim()` (crud/agreement_receipt.py),
    which is deliberately written to make NO assumption about its caller's
    session behavior, THIS function's atomicity is not self-contained — it
    releases first and claims second, two separate flushes with no
    savepoint between them, so "the release happened but the reclaim
    failed" is a real intermediate state this function can pass through.
    Whether that intermediate state ever reaches the database (vs. being
    rolled back as if it never happened) depends entirely on the CALLER's
    session. The only caller today is `PUT /invoices/{id}/receipts`
    (api/v1/invoices.py), whose session is a request-scoped one from
    app/db/session.py's get_session dependency — it rolls back on any raised
    exception, which is what makes a rejected PUT observably a no-op end to
    end. A caller with a session that does NOT roll back on ValueError would
    not get that guarantee for free from this function alone.
    """
    if await _invoice_referenced_by_active_pa(db, invoice.id):
        raise ValueError(
            "This invoice is already referenced by a payment application; "
            "its receipt evidence can no longer be changed here.")

    agr = (await db.execute(
        select(PurchaseAgreement).where(PurchaseAgreement.id == invoice.agreement_id)
    )).scalar_one_or_none()
    if agr is None:
        raise ValueError("Invoice is not matched to an agreement")

    await _release_agreement_evidence(db, invoice)

    if receipt_ids:
        claimed = await agreement_receipt_crud.claim(db, agr, receipt_ids, invoice)
        invoice.receipt_ids = [str(r.id) for r in claimed]
        invoice.receipt_variance_reason = (variance_reason or "").strip() or None
        # 挂上了凭证就不再是无凭证结算 —— 这两个状态互斥,协议详情页那个
        # 健康度计数依赖它们互斥才有意义。
        invoice.legacy_settlement = False
        invoice.legacy_settlement_reason = None
    await db.flush()
    return invoice


async def settle_without_receipt(
    db: AsyncSession, invoice: Invoice, reason: str,
) -> Invoice:
    """显式声明这张发票没有任何签收凭证(Task 7:PA 闸门在起付款时要求要么
    有凭证、要么有这个声明——见 api/v1/pa.py)。

    先释放它可能还持有的凭证:一张自称无凭证的发票不该继续锁着几份真凭证,
    那些凭证会永久卡在 reconciled 且没有任何界面能放它们回来(update()/void()
    都拒绝该状态)。

    Fix-round 1 (Important #1): refuses an invoice a non-cancelled PA already
    references, same as set_receipts — otherwise this could plant an
    irreversible "settled without receipt" flag on an invoice that has
    already cleared payment.

    Fix-round 1 (Minor #5): mirrors set_receipts' agreement_id check
    (Important #1's sibling gap) — without it, a plain PO-matched invoice
    (agreement_id NULL, already 3-way matched with a real GR) could be
    stamped legacy_settlement=True too. That flag doesn't bypass anything
    (api/v1/pa.py only reads it inside the house_account branch), but
    InvoiceDetailPage.tsx renders "settled without receipt evidence" on it
    regardless of route — a false, confusing claim on an invoice that has
    perfectly good GR evidence.
    """
    if await _invoice_referenced_by_active_pa(db, invoice.id):
        raise ValueError(
            "This invoice is already referenced by a payment application; "
            "its settlement status can no longer be changed here.")
    agr = (await db.execute(
        select(PurchaseAgreement).where(PurchaseAgreement.id == invoice.agreement_id)
    )).scalar_one_or_none()
    if agr is None:
        raise ValueError("Invoice is not matched to an agreement")

    await _release_agreement_evidence(db, invoice)
    invoice.legacy_settlement = True
    invoice.legacy_settlement_reason = reason.strip()
    await db.flush()
    return invoice


async def match(
    db: AsyncSession,
    invoice: Invoice,
    req: InvoiceMatchRequest,
    matched_by: uuid.UUID,
    require_review: bool = False,
    auto_link_grs: bool = False,
) -> Invoice:
    # Agreement route short-circuits: it shares none of the PO allocation
    # machinery (no lines, no GRs, no balance check), and running that first
    # would reject a valid monthly statement.
    if req.agreement_id is not None:
        return await _match_to_agreement(db, invoice, req, matched_by, require_review)

    # Symmetric cleanup for the agreement→PO direction (code review finding,
    # 2026-08-07): _match_to_agreement above clears every PO field on a route
    # switch; this is the mirror image. Without it an invoice moved back to
    # the PO route would keep pointing at a stale agreement — inflating that
    # agreement's NTE/consumed_amount with an invoice it no longer backs,
    # while ALSO carrying PO fields. Currently unreachable through the API
    # (POST /match 409s on an already-"matched" invoice — which every
    # agreement match produces — and PATCH's rematch path only fires when
    # po_id/gr_ids are already set, neither true for an agreement invoice),
    # but crud.match() must hold this invariant regardless of which future
    # caller reaches it.
    #
    # 1B addendum (code review finding): schedule_id is the same kind of
    # stale link — a claimed AgreementPaymentSchedule row must be released
    # back to "pending"/invoice_id=None, not left permanently marked
    # "received" against an invoice that no longer backs it. Left alone, the
    # schedule would silently under-report what is still owed and no later
    # invoice could ever claim that period/milestone again.
    previous_agreement_id = invoice.agreement_id
    if previous_agreement_id is not None:
        invoice.agreement_id = None
        invoice.agreement_number = None
        invoice.legacy_settlement = False
        invoice.legacy_settlement_reason = None
        await _release_agreement_evidence(db, invoice)
    invoice.match_route = "po"
    invoice.match_route_auto = False

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
        if ref_po.vendor_id != invoice.vendor_id:
            raise FeeOnlyLinkRequired("Linked PO must belong to the same vendor as the invoice")
        invoice.po_id = ref_po.id
        invoice.po_number = ref_po.number
        invoice.po_total = Decimal("0")
        invoice.variance = Decimal("0")
        invoice.variance_pct = Decimal("0")
        # Clear any stale reason from a previous (e.g. exception) match state —
        # re-matching a previously-flagged invoice as fee-only shouldn't carry it
        # forward now that variance is definitionally zero.
        invoice.exception_reason = None

    invoice.matched_po_line_ids = None
    invoice.matched_reference_total = None

    # GR handling (whole-invoice level). An explicit selection always wins — that
    # includes an explicit empty one, which is why auto-discovery is opt-in per
    # call site: rematch_from_existing replays the invoice's stored gr_ids, so a
    # user who cleared the link in the edit form must not have it grow back.
    effective_gr_ids: list[uuid.UUID] = []
    if req.gr_ids:
        effective_gr_ids = list(req.gr_ids)
    elif req.gr_id:
        effective_gr_ids = [req.gr_id]
    elif auto_link_grs:
        effective_gr_ids = await _discover_grs_for_allocations(db, allocs)
    await _apply_gr_selection(db, invoice, effective_gr_ids)

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
    if previous_agreement_id is not None:
        # This invoice no longer counts against the agreement it used to
        # settle against — release it, same as the agreement branch does when
        # moving between two agreements.
        await _recompute_consumed(db, previous_agreement_id)
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
    released_agreement_id: uuid.UUID | None = None
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
        #
        # The agreement route cannot keep the same "leave it for reference"
        # convention: consumed_amount is derived from every invoice whose
        # agreement_id is still set, with no status filter (a rejected match
        # was never a real spend event and must not count toward the NTE
        # ceiling). This branch only becomes reachable now that
        # require_review can route an agreement match through match_review —
        # before that, an agreement-linked invoice was always "matched", so a
        # reject was never possible while agreement_id stayed set. Detaching
        # the invoice fully (rather than filtering consumed_amount by status)
        # mirrors the same cleanup match()'s PO branch already performs on a
        # route switch, and keeps _recompute_consumed's "every linked invoice
        # counts" contract simple and honest.
        released_agreement_id = invoice.agreement_id
        if released_agreement_id is not None:
            invoice.agreement_id = None
            invoice.agreement_number = None
            invoice.match_route = None
            invoice.legacy_settlement = False
            invoice.legacy_settlement_reason = None
            # Code review finding (Task 7 fix round): a delegate can claim a
            # REAL period (schedule_id set, row status="received") and still
            # land in match_review (require_review is delegate-driven, not
            # only "no claimable row") — rejecting that match must release
            # the row the same way match()'s agreement→PO switch already
            # does, or the period stays permanently "received" against an
            # invoice that no longer backs it and can never be claimed again.
            await _release_agreement_evidence(db, invoice)
    await db.flush()
    await db.refresh(invoice)
    if released_agreement_id is not None:
        await _recompute_consumed(db, released_agreement_id)
        await db.flush()
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
    # GR selection from the edit form (only sent when a PO is linked). Derive the
    # scalar gr_id / gr_number / gr_value here rather than relying solely on the
    # later rematch_from_existing → match() to re-derive them: that path returns
    # early for fee-only / reference-only invoices (no allocation rows), which
    # would leave gr_id NULL and break the 3-Way Match view + PA receipt gate.
    # match() recomputes the same values idempotently for allocated invoices.
    # [] means "clear all GRs".
    if payload.gr_ids is not None:
        await _apply_gr_selection(db, invoice, list(payload.gr_ids))
    invoice.total_amount = invoice.amount + invoice.tax_amount
    await db.flush()
    # Agreement route: total_amount can change here (amount/tax edit) on an
    # already-"matched" agreement invoice without ever going back through
    # match() — rematch_from_existing below only fires when po_id or gr_ids
    # are already set, neither of which an agreement invoice carries. Without
    # this, consumed_amount silently drifts from the edited total and both the
    # NTE warning and the progress bar under-report (code review finding,
    # 2026-08-07).
    if invoice.agreement_id is not None:
        await _recompute_consumed(db, invoice.agreement_id)
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
    agreement_id = invoice.agreement_id
    # Whole-branch review (I4): release the agreement-side evidence BEFORE the
    # row goes away — same helper, same order as the Data Maintenance delete
    # path (app/admin/registry.py::_invoice_delete). It was only ever wired up
    # over there, so the two delete paths disagreed.
    #
    # Reachable despite the status guard above: neither
    # agreement_payment_schedule.invoice_id nor agreement_receipts
    # .invoice_id has an FK back to invoices (shared table, three other
    # services touch it), and Data Maintenance declares invoice.status an
    # editable enum including "unmatched" (registry.py) applied by a bare
    # setattr with no hooks (admin/service.py) — resetting a matched invoice
    # to "unmatched" for a re-match is the documented way to do that, and it
    # is exactly the path the comment in match() above already admits exists.
    # Delete it in that state and the receipts are stranded "reconciled"
    # pointing at a row that no longer exists: update() and void() both
    # refuse a reconciled receipt, and _release_agreement_evidence can only
    # ever discover them through invoice.receipt_ids — which just got
    # deleted. No invoice can ever claim those receipts again.
    await _release_agreement_evidence(db, invoice)
    await db.delete(invoice)
    await db.flush()
    # If this was the last invoice keeping a create_pa task alive for its PO,
    # complete that task so it doesn't linger in the requester's inbox.
    if po_id is not None:
        from app.crud.task import _complete_orphan_create_pa_tasks
        await _complete_orphan_create_pa_tasks(db, po_id)
    # Agreement route: the FK (purchase_agreements.id ← invoices.agreement_id)
    # is ondelete="RESTRICT" on the AGREEMENT side only — it blocks deleting
    # the agreement while invoices reference it, it does NOT block deleting
    # the invoice. A hard-deleted invoice must not leave the agreement's
    # consumed_amount permanently inflated by a row that no longer exists
    # (code review finding, 2026-08-07).
    if agreement_id is not None:
        await _recompute_consumed(db, agreement_id)


# ── Status update ──────────────────────────────────────────────────────────────

async def update_status(db: AsyncSession, invoice: Invoice, status: str) -> Invoice:
    invoice.status = status
    await db.flush()
    await db.refresh(invoice)
    return invoice
