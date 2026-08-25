"""CRUD + workflow for Purchase Order (PO)."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._numbering import next_number
from app.models.approval import ApprovalEvent
from app.models.config import CompanyConfig
from app.models.cost_center import CostCenter
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User
from app.schemas.po import PO_WORKFLOW, PlaceOrderRequest, PoActionRequest, PoCreate, PoImportedDetailsUpdate, PoUpdate
from app.schemas.pr import ApprovalEventResponse


# ── Number generation ──────────────────────────────────────────────────────────

async def _next_number(db: AsyncSession, vendor_code: str) -> str:
    ym = datetime.now(timezone.utc).strftime("%y%m")
    prefix = f"PO-{vendor_code}-{ym}-"
    return await next_number(db, PurchaseOrder.number, prefix, width=2)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_line_items(po_id: uuid.UUID, items_in) -> list[PoLineItem]:
    return [
        PoLineItem(
            po_id=po_id,
            pr_line_id=item.pr_line_id,
            description=item.description,
            material_id=item.material_id,
            supplier_item_id=item.supplier_item_id,
            qty=item.qty,
            unit=item.unit,
            unit_price=item.unit_price,
            line_total=item.line_total,
            notes=item.notes,
            sort_order=i,
        )
        for i, item in enumerate(items_in)
    ]


def _compute_totals(items_in, tax_rate: Decimal):
    subtotal = sum((item.line_total for item in items_in), Decimal("0"))
    tax_amount = (subtotal * tax_rate).quantize(Decimal("0.01"))
    total = subtotal + tax_amount
    return subtotal, tax_amount, total


# ── Reads ──────────────────────────────────────────────────────────────────────

async def get_all(
    db: AsyncSession,
    *,
    status: str | None = None,
    vendor_id: uuid.UUID | None = None,
    pr_id: uuid.UUID | None = None,
    pr_type: int | None = None,
    department_id: uuid.UUID | None = None,
    is_prepaid: bool | None = None,
    created_by: uuid.UUID | None = None,
    search: str | None = None,
    po_ids_subq=None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[PurchaseOrder], int]:
    q = select(PurchaseOrder)
    if po_ids_subq is not None:
        q = q.where(PurchaseOrder.id.in_(po_ids_subq))
    if status:
        q = q.where(PurchaseOrder.status == status)
    if vendor_id:
        q = q.where(PurchaseOrder.vendor_id == vendor_id)
    if pr_id:
        q = q.where(PurchaseOrder.pr_id == pr_id)
    if pr_type:
        q = q.where(PurchaseOrder.type == pr_type)
    if is_prepaid is not None:
        q = q.where(PurchaseOrder.is_prepaid == is_prepaid)
    if department_id:
        # PO has no cost center of its own — resolve department via the linked PR.
        q = q.where(PurchaseOrder.pr_id.in_(
            select(PurchaseRequest.id).where(PurchaseRequest.cost_center_id.in_(
                select(CostCenter.id).where(CostCenter.department_id == department_id)
            ))
        ))
    if created_by:
        q = q.where(PurchaseOrder.created_by == created_by)
    if search:
        term = f"%{search}%"
        # Match what the UI advertises: PO#, title, vendor name, budget code.
        # vendor_name is a NOT NULL snapshot on the PO, so no join needed.
        q = q.where(
            PurchaseOrder.title.ilike(term)
            | PurchaseOrder.number.ilike(term)
            | PurchaseOrder.vendor_name.ilike(term)
            | PurchaseOrder.budget_code.ilike(term)
        )
    total: int = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    offset = (page - 1) * page_size
    items = list((await db.execute(
        q.order_by(PurchaseOrder.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all())
    return items, total


async def get_by_id(db: AsyncSession, po_id: uuid.UUID) -> PurchaseOrder | None:
    result = await db.execute(
        select(PurchaseOrder, User.full_name)
        .outerjoin(User, User.id == PurchaseOrder.created_by)
        .where(PurchaseOrder.id == po_id)
    )
    row = result.first()
    if row is None:
        return None
    po, creator_name = row
    po.created_by_name = creator_name  # transient attr consumed by PoResponse
    return po


# ── Create ─────────────────────────────────────────────────────────────────────

async def create(
    db: AsyncSession,
    payload: PoCreate,
    vendor_code: str,
    vendor_name: str,
    created_by: uuid.UUID,
) -> PurchaseOrder:
    number = await _next_number(db, vendor_code)
    subtotal, tax_amount, total = _compute_totals(payload.line_items, payload.tax_rate)

    # Resolve PR number if pr_id provided
    pr_number: str | None = None
    if payload.pr_id:
        pr_result = await db.execute(
            select(PurchaseRequest.number).where(PurchaseRequest.id == payload.pr_id)
        )
        pr_number = pr_result.scalar_one_or_none()

    po = PurchaseOrder(
        number=number,
        title=payload.title,
        type=payload.type,
        vendor_id=payload.vendor_id,
        vendor_name=vendor_name,
        currency=payload.currency,
        subtotal=subtotal,
        tax_rate=payload.tax_rate,
        tax_code=payload.tax_code,
        tax_amount=tax_amount,
        total=total,
        budget_code=payload.budget_code,
        expected_delivery=payload.expected_delivery,
        delivery_address=payload.delivery_address,
        notes=payload.notes,
        pr_id=payload.pr_id,
        pr_number=pr_number,
        is_prepaid=getattr(payload, "is_prepaid", False) or False,
        created_by=created_by,
    )
    db.add(po)
    await db.flush()

    for item in _build_line_items(po.id, payload.line_items):
        db.add(item)

    # Link back to PR if provided and complete its create_po task
    if payload.pr_id:
        pr_result = await db.execute(
            select(PurchaseRequest).where(PurchaseRequest.id == payload.pr_id)
        )
        pr = pr_result.scalar_one_or_none()
        if pr:
            pr.po_id = po.id
            pr.po_number = number
        await _complete_tasks(db, "pr", payload.pr_id)

    await db.flush()
    await db.refresh(po)
    return po


# ── Update (draft / returned only) ────────────────────────────────────────────

async def update(
    db: AsyncSession,
    po: PurchaseOrder,
    payload: PoUpdate,
    vendor_code: str | None = None,
    vendor_name: str | None = None,
) -> PurchaseOrder:
    for field in ("title", "type", "currency", "budget_code",
                  "expected_delivery", "delivery_address", "notes", "is_prepaid"):
        val = getattr(payload, field)
        if val is not None:
            setattr(po, field, val)

    if payload.vendor_id is not None and vendor_name is not None:
        po.vendor_id = payload.vendor_id
        po.vendor_name = vendor_name

    tax_rate = payload.tax_rate if payload.tax_rate is not None else po.tax_rate
    if payload.tax_code is not None:
        po.tax_code = payload.tax_code

    if payload.line_items is not None:
        for old in list(po.line_items):
            await db.delete(old)
        await db.flush()
        for item in _build_line_items(po.id, payload.line_items):
            db.add(item)
        subtotal, tax_amount, total = _compute_totals(payload.line_items, tax_rate)
        po.subtotal = subtotal
        po.tax_rate = tax_rate
        po.tax_amount = tax_amount
        po.total = total
    elif payload.tax_rate is not None:
        po.tax_rate = tax_rate
        items = po.line_items
        subtotal = sum((item.line_total for item in items), Decimal("0"))
        po.tax_amount = (subtotal * tax_rate).quantize(Decimal("0.01"))
        po.total = po.subtotal + po.tax_amount

    await db.flush()
    await db.refresh(po)
    return po


# ── Imported-PO buyer details (NC mirror, any status) ────────────────────────

async def has_any_invoice(db: AsyncSession, po_id: uuid.UUID) -> bool:
    """True once ANY invoice points at this PO.

    The same broad definition the NC writer freezes a PO on (`_po_consumed`) and
    the full reload preserves on: draft, matched, partially_paid or paid alike.
    A PO the accounts-payable flow has started measuring against is one whose
    header money must stop moving, whatever stage that flow has reached.
    """
    row = await db.execute(select(Invoice.id).where(Invoice.po_id == po_id).limit(1))
    return row.scalar_one_or_none() is not None



def manual_lines_total(po: PurchaseOrder) -> Decimal:
    """What the buyer-added lines on this PO come to, pre-tax.

    Pre-tax because that is what ``purchase_orders.subtotal`` is, and the
    subtotal is the figure the header arithmetic and the invoice variance are
    both measured in. (NC's own mirrored lines store a TAX-INCLUSIVE line_total
    — norigtaxmny — which differs from their pre-tax amount only on the four
    in-scope orders that carry any tax at all. Manual lines are entered pre-tax
    and their line_total is pre-tax, so this sum can go straight onto subtotal.)
    """
    return sum((ln.line_total for ln in po.line_items if ln.nc_source_pk is None),
               Decimal("0"))


def manual_lines_changed(po: PurchaseOrder, entries) -> bool:
    """Whether ``entries`` would actually alter this PO's buyer-added lines.

    The editor re-sends the complete manual set on every save, including a save
    that only touched Incoterms — so the invoice guard has to ask whether the
    set DIFFERS, not whether the key is present. Guarding on presence would make
    an invoiced PO unsavable, which is the same trap the tax-rate guard had to
    step around.
    """
    stored = {ln.id: ln for ln in po.line_items if ln.nc_source_pk is None}
    submitted_ids = {e.id for e in entries if e.id is not None}
    if submitted_ids != set(stored) or len(entries) != len(stored):
        return True                       # something added, removed, or foreign
    for entry in entries:
        line = stored[entry.id]
        if (line.description != entry.description
                or line.qty != entry.qty
                or line.unit != entry.unit
                or line.unit_price != entry.unit_price
                or line.supplier_item_id != entry.supplier_item_id
                or line.sample != entry.sample):
            return True
    return False


def _apply_manual_lines(po: PurchaseOrder, entries) -> list[dict]:
    """Reconcile the PO's buyer-added lines to ``entries``. Returns audit deltas.

    ``entries`` is the complete desired set, so a stored manual line that is not
    in it is deleted (the relationship is delete-orphan, so dropping it from the
    collection is the delete).

    Raises ValueError — surfaced as a 400, never a 500 — for an id that is not
    one of THIS PO's manual lines. That covers two different attacks with one
    check: an NC-owned line, whose description/quantity/price the ERP owns and
    this endpoint must never rewrite, and a line belonging to another document,
    which would make this a cross-document write primitive.
    """
    stored = {ln.id: ln for ln in po.line_items if ln.nc_source_pk is None}
    nc_ids = {ln.id for ln in po.line_items if ln.nc_source_pk is not None}
    # Added lines sort after everything NC sent, so the ERP's own line order —
    # which the supplier's copy is read against — is never disturbed.
    next_sort = max((ln.sort_order for ln in po.line_items), default=-1) + 1

    changes: list[dict] = []
    kept: set[uuid.UUID] = set()
    for entry in entries:
        if entry.id is not None and entry.id in nc_ids:
            raise ValueError(
                f"Line {entry.id} comes from NC and cannot be edited here")
        if entry.id is not None and entry.id not in stored:
            raise ValueError(f"Line {entry.id} does not belong to PO {po.number}")
        line_total = (entry.qty * entry.unit_price).quantize(Decimal("0.01"))
        if entry.id is None:
            po.line_items.append(PoLineItem(
                po_id=po.id, description=entry.description, material_id=None,
                supplier_item_id=entry.supplier_item_id, sample=entry.sample,
                qty=entry.qty, unit=entry.unit, unit_price=entry.unit_price,
                line_total=line_total, received_qty=Decimal("0"),
                sort_order=next_sort, nc_source_pk=None,
            ))
            next_sort += 1
            changes.append({"added": entry.description, "line_total": str(line_total)})
            continue
        line = stored[entry.id]
        kept.add(entry.id)
        delta: dict = {}
        for field, value in (("description", entry.description),
                             ("qty", entry.qty),
                             ("unit", entry.unit),
                             ("unit_price", entry.unit_price),
                             ("line_total", line_total),
                             ("supplier_item_id", entry.supplier_item_id),
                             ("sample", entry.sample)):
            old = getattr(line, field)
            if old != value:
                delta[field] = [str(old) if old is not None else None,
                                str(value) if value is not None else None]
                setattr(line, field, value)
        if delta:
            changes.append({"line_id": str(entry.id), **delta})

    for line_id, line in stored.items():
        if line_id not in kept:
            changes.append({"removed": line.description,
                            "line_total": str(line.line_total)})
            po.line_items.remove(line)
    return changes


async def update_imported_details(
    db: AsyncSession,
    po: PurchaseOrder,
    payload: PoImportedDetailsUpdate,
) -> tuple[PurchaseOrder, dict, dict]:
    """Apply buyer-supplied detail to an NC-imported PO.

    Only writes the columns named in PoImportedDetailsUpdate. subtotal is never
    touched: a tax-rate change re-derives tax_amount/total from the existing
    subtotal so the header stays internally consistent.

    Returns (po, before, after) holding only the fields this call actually
    changed, for the caller's audit-log entry. Raises ValueError if a line id
    does not belong to this PO.
    """
    before: dict = {}
    after: dict = {}
    # Captured before anything mutates, so the audit entry and the recompute
    # below both read the figures this call started from.
    _orig_subtotal, _orig_tax, _orig_total = po.subtotal, po.tax_amount, po.total
    # pydantic v2: which keys the caller's JSON body actually contained. A key
    # present with an explicit null must clear a nullable column; a key
    # absent from the body must leave the column untouched — those are not
    # the same thing, and collapsing them (checking `value is None` alone)
    # made a nullable field impossible to ever clear from the frontend.
    fields_set = payload.model_fields_set

    def _set(field: str, value, *, nullable: bool = True) -> None:
        if field not in fields_set:
            return
        if value is None and not nullable:
            # NOT NULL column (is_prepaid) — an explicit null on the wire has
            # no column state to map to, so it is a no-op, not a clear.
            return
        old = getattr(po, field)
        if old == value:
            return
        before[field] = str(old) if old is not None else None
        after[field] = str(value) if value is not None else None
        setattr(po, field, value)

    for field in ("expected_delivery", "delivery_address", "incoterms",
                  "tax_code", "buyer_notes"):
        _set(field, getattr(payload, field))
    _set("is_prepaid", payload.is_prepaid, nullable=False)

    # Buyer-added lines, before the money is recomputed — they are one of its
    # two terms.
    manual_before = manual_lines_total(po)
    if "manual_lines" in fields_set and payload.manual_lines is not None:
        manual_delta = _apply_manual_lines(po, payload.manual_lines)
        if manual_delta:
            after["manual_lines"] = manual_delta
    manual_after = manual_lines_total(po)

    if manual_after != manual_before:
        # NC's own subtotal is not stored separately and does not need to be:
        # the stored figure is NC's plus whatever the manual lines came to, and
        # those are recoverable from the rows at any moment. Subtracting the
        # previous manual term recovers NC's, and the new term goes back on top
        # — so this stays exact across any number of edits, and the writer can
        # reconstruct the same figure from NC's side after a re-sync.
        po.subtotal = (_orig_subtotal - manual_before) + manual_after
        before["subtotal"] = str(_orig_subtotal)
        after["subtotal"] = str(po.subtotal)

    # tax_rate is NOT NULL too (default 0), so an explicit null is likewise a
    # no-op rather than a clear — only a present, non-null rate is applied.
    rate_changed = ("tax_rate" in fields_set and payload.tax_rate is not None
                    and payload.tax_rate != po.tax_rate)
    if rate_changed:
        before["tax_rate"] = str(po.tax_rate)
        po.tax_rate = payload.tax_rate
        after["tax_rate"] = str(po.tax_rate)
    if rate_changed or manual_after != manual_before:
        # Either term can move the tax: a new rate, or a new subtotal to apply
        # the existing rate to. Recomputed in one place so the header can never
        # hold a total that disagrees with its own subtotal and rate.
        before["tax_amount"] = str(_orig_tax)
        before["total"] = str(_orig_total)
        po.tax_amount = (po.subtotal * po.tax_rate).quantize(Decimal("0.01"))
        po.total = po.subtotal + po.tax_amount
        after["tax_amount"] = str(po.tax_amount)
        after["total"] = str(po.total)

    by_id = {line.id: line for line in po.line_items}
    line_changes: list[dict] = []
    for patch in payload.lines:
        line = by_id.get(patch.id)
        if line is None:
            # Never a 500 and never a silent no-op: a line id from another PO is
            # a caller error worth surfacing, and letting it through would make
            # this endpoint a cross-document write primitive.
            raise ValueError(f"Line {patch.id} does not belong to PO {po.number}")
        delta: dict = {}
        line_fields_set = patch.model_fields_set
        if "supplier_item_id" in line_fields_set and line.supplier_item_id != patch.supplier_item_id:
            delta["supplier_item_id"] = [line.supplier_item_id, patch.supplier_item_id]
            line.supplier_item_id = patch.supplier_item_id
        if "sample" in line_fields_set and line.sample != patch.sample:
            delta["sample"] = [line.sample, patch.sample]
            line.sample = patch.sample
        if delta:
            line_changes.append({"line_id": str(patch.id), **delta})
    if line_changes:
        after["lines"] = line_changes

    if "tax_rate" in after:
        # Marks the PO for nc_purchase_sync/writer.py's tax-rate guard, which
        # reads this column as "the tax rate was set by hand" and keeps it
        # (re-deriving tax_amount/total from NC's fresh subtotal) instead of
        # letting NC's own tax_rate/tax_amount/total overwrite it. Gated
        # specifically on the tax rate having changed — NOT on `if after:` —
        # because an edit that only touches e.g. Incoterms or a line's
        # Supplier Item ID must not freeze NC's tax on this PO forever.
        before["buyer_edited_at"] = (
            po.buyer_edited_at.isoformat() if po.buyer_edited_at is not None else None
        )
        po.buyer_edited_at = datetime.now(timezone.utc)
        after["buyer_edited_at"] = po.buyer_edited_at.isoformat()

    await db.flush()
    await db.refresh(po)
    return po, before, after


# ── Config helpers ─────────────────────────────────────────────────────────────

async def _get_config(db: AsyncSession) -> CompanyConfig | None:
    result = await db.execute(select(CompanyConfig).limit(1))
    return result.scalar_one_or_none()


async def _get_po_workflow(db: AsyncSession) -> list[dict]:
    """Load PO workflow from company config; falls back to hardcoded PO_WORKFLOW."""
    cfg = await _get_config(db)
    if cfg and cfg.workflow_defs:
        nodes = cfg.workflow_defs.get("po", [])
        if nodes:
            return nodes
    return PO_WORKFLOW


# ── Workflow actions ───────────────────────────────────────────────────────────

async def action(
    db: AsyncSession,
    po: PurchaseOrder,
    req: PoActionRequest,
    actor_id: uuid.UUID,
    actor_role: str,
) -> PurchaseOrder:
    act = req.action.lower()
    step = po.approval_step_idx
    workflow = await _get_po_workflow(db)

    # For approve steps, record the workflow step's role (not the JWT base role)
    # so that multi-role users (e.g. dept_manager who is also procurement_manager)
    # appear correctly in the audit history.
    recorded_role = actor_role

    if act == "submit":
        if po.status not in ("draft", "returned"):
            raise ValueError(f"Cannot submit PO in status '{po.status}'")
        po.status = "submitted"
        po.approval_step_idx = 0
        await _create_task(db, po, step=0, workflow=workflow)

    elif act == "approve":
        if po.status not in ("submitted", "in_review"):
            raise ValueError(f"Cannot approve PO in status '{po.status}'")
        recorded_role = workflow[step]["role"] if step < len(workflow) else actor_role
        await _complete_tasks(db, "po", po.id)

        # Build role → holder-set mapping for auto-skip logic. A post can be
        # held via PRIMARY role (users.role) OR an ADDITIONAL role (identity's
        # user_roles, same physical DB) — phase 3 retired the old single
        # company_config.role_management.<role>_user_id fields.
        from app.core.access_scope import role_holder_ids
        role_holders = await role_holder_ids(db)

        def _actor_holds_role_po(role: str) -> bool:
            return actor_id in role_holders.get(role, set())

        next_step = step + 1
        # Auto-skip any consecutive steps where the same actor holds the assigned role.
        while next_step < len(workflow) and _actor_holds_role_po(workflow[next_step]["role"]):
            auto_role = workflow[next_step]["role"]
            db.add(ApprovalEvent(
                document_type="po",
                document_id=po.id,
                document_number=po.number,
                step_idx=next_step,
                action="approve",
                actor_id=actor_id,
                actor_role=auto_role,
                comment="Auto-approved (same approver holds both roles)",
            ))
            next_step += 1

        if next_step < len(workflow):
            po.approval_step_idx = next_step
            po.status = "in_review"
            await _create_task(db, po, step=next_step, workflow=workflow)
        else:
            po.status = "approved"
            await _create_place_order_task(db, po)
            if po.is_prepaid:
                await _create_prepayment_pa_task(db, po)

    elif act == "return":
        if po.status not in ("submitted", "in_review"):
            raise ValueError(f"Cannot return PO in status '{po.status}'")
        await _complete_tasks(db, "po", po.id)
        po.status = "returned"
        po.approval_step_idx = 0
        await _create_revise_task(db, po)

    elif act == "reject":
        if po.status not in ("submitted", "in_review"):
            raise ValueError(f"Cannot reject PO in status '{po.status}'")
        await _complete_tasks(db, "po", po.id)
        po.status = "rejected"

    elif act == "issue":
        if po.status != "approved":
            raise ValueError(f"Cannot issue PO in status '{po.status}'")
        po.status = "issued"

    elif act == "cancel":
        if po.status not in ("draft", "returned", "submitted"):
            raise ValueError(f"Cannot cancel PO in status '{po.status}'")
        await _complete_tasks(db, "po", po.id)
        po.status = "cancelled"

    else:
        raise ValueError(f"Unknown action '{act}'")

    db.add(ApprovalEvent(
        document_type="po",
        document_id=po.id,
        document_number=po.number,
        step_idx=step,
        action=act,
        actor_id=actor_id,
        actor_role=recorded_role,
        comment=req.comment,
    ))

    await db.flush()
    await db.refresh(po)
    return po


# ── Task helpers ───────────────────────────────────────────────────────────────

async def _create_task(db: AsyncSession, po: PurchaseOrder, step: int, workflow: list[dict]) -> None:
    wf = workflow[step]
    db.add(Task(
        type="approve_po",
        priority="normal",
        document_type="po",
        document_id=po.id,
        document_number=po.number,
        assigned_role=wf["role"],
        title=f"Approve PO: {po.number} — {po.title}",
        description=f"Step {step + 1}/{len(workflow)}: {wf['label']} review required.",
        amount=po.total,
        vendor=po.vendor_name,
    ))


async def _create_place_order_task(db: AsyncSession, po: PurchaseOrder) -> None:
    db.add(Task(
        type="place_order",
        priority="normal",
        document_type="po",
        document_id=po.id,
        document_number=po.number,
        assigned_role="procurement_officer",
        title=f"Place Order: {po.number} — {po.title}",
        description=f"PO {po.number} has been approved. Please place the order with the vendor.",
        amount=po.total,
        vendor=po.vendor_name,
    ))


async def _create_prepayment_pa_task(db: AsyncSession, po: PurchaseOrder) -> None:
    """Remind the requester (or PO creator) to create a Prepayment PA.

    assigned_user_id is always set to a specific user so the task is never
    role-broadcast to all Requesters (bug: assigned_user_id=None +
    assigned_role="requester" makes the task visible to everyone with that role).
    Priority: PR creator → PO creator (fallback for direct POs).
    """
    from app.models.pr import PurchaseRequest
    requester_id: uuid.UUID | None = None
    if po.pr_id:
        result = await db.execute(
            select(PurchaseRequest.created_by).where(PurchaseRequest.id == po.pr_id)
        )
        requester_id = result.scalar_one_or_none()
    # Always assign to a specific user — never leave None (which would broadcast).
    if requester_id is None:
        requester_id = po.created_by
    db.add(Task(
        type="create_prepayment_pa",
        priority="normal",
        document_type="po",
        document_id=po.id,
        document_number=po.number,
        assigned_role="requester",
        assigned_user_id=requester_id,
        title=f"Create Prepayment PA: {po.number} — {po.title}",
        description=(
            f"PO {po.number} has been approved and requires an advance payment. "
            "Please create a Prepayment Payment Application before goods are delivered."
        ),
        amount=po.total,
        vendor=po.vendor_name,
    ))


async def _create_revise_task(db: AsyncSession, po: PurchaseOrder) -> None:
    db.add(Task(
        type="revise_po",
        priority="normal",
        document_type="po",
        document_id=po.id,
        document_number=po.number,
        assigned_role="procurement_officer",
        assigned_user_id=po.created_by,
        title=f"Revise PO: {po.number} — {po.title}",
        description="Your PO has been returned for revision.",
        amount=po.total,
        vendor=po.vendor_name,
    ))


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


async def place_order(
    db: AsyncSession,
    po: PurchaseOrder,
    req: PlaceOrderRequest,
    actor_id: uuid.UUID,
    actor_role: str,
    vendor_email: str | None = None,
) -> PurchaseOrder:
    """Transition PO from approved → issued and record placement method."""
    if po.status != "approved":
        raise ValueError(f"Cannot place order for PO in status '{po.status}'")

    now = datetime.now(timezone.utc)
    po.status = "issued"
    po.place_order_method = req.method
    po.placed_at = now

    if req.method == "email":
        po.place_order_email_to = req.to or vendor_email
    elif req.method == "online":
        po.place_order_reference = req.reference

    await _complete_tasks(db, "po", po.id)

    db.add(ApprovalEvent(
        document_type="po",
        document_id=po.id,
        document_number=po.number,
        step_idx=po.approval_step_idx,
        action="issue",
        actor_id=actor_id,
        actor_role=actor_role,
        comment=(
            f"Order placed via email to {po.place_order_email_to}" if req.method == "email"
            else f"Order placed online{(' — ref: ' + req.reference) if req.reference else ''}"
        ),
    ))

    await db.flush()
    await db.refresh(po)
    return po


async def get_approval_events(
    db: AsyncSession, po_id: uuid.UUID
) -> list[ApprovalEventResponse]:
    result = await db.execute(
        select(ApprovalEvent, User.full_name)
        .outerjoin(User, User.id == ApprovalEvent.actor_id)
        .where(ApprovalEvent.document_type == "po", ApprovalEvent.document_id == po_id)
        .order_by(ApprovalEvent.created_at)
    )
    return [
        ApprovalEventResponse(
            **{c: getattr(ev, c) for c in ApprovalEventResponse.model_fields if c != "actor_name"},
            actor_name=full_name,
        )
        for ev, full_name in result.all()
    ]


async def po_has_three_way_matched_invoice(db: AsyncSession, po_id: uuid.UUID) -> bool:
    """True 当 PO 有任一张 3-way matched 发票:status=='matched' 且已收货。

    这是 PA 收货闸门 / create_pa 触发 / confirm_receipt 停发 的统一真相源。
    GR 一创建即由 _autofill_gr_to_matched_invoices 写 gr_id → 立即达成 3-way,
    不要求收货确认(collected/confirmed)。

    发票挂到 PO 有两种方式,两种都算(与 crud.invoice.get_all 的可见性口径、
    api.v1.pa 的「发票是否属于本 PO」校验一致):
      1. 表头直连 Invoice.po_id —— 收货证据用发票自己的 gr_id(原口径不变)。
      2. 行级分摊 invoice_po_allocations(一票多 PO)—— 此时发票的 gr_id 可能
         指向**别的** PO 的 GR,拿它当本 PO 的收货证据会放行未收货的付款,
         所以要求本 PO 自己有 GR。
    只认表头会让分摊 PO 永远拿不到 create_pa 且被闸门 422 挡住(生产
    PO-400-2607-12:发票表头是 PO-400-2607-11,242 元静默漏付)。
    """
    header = (await db.execute(
        select(Invoice.id).where(
            Invoice.po_id == po_id,
            Invoice.status == "matched",
            Invoice.gr_id.is_not(None),
        ).limit(1)
    )).scalar_one_or_none()
    if header is not None:
        return True

    allocated = (await db.execute(
        select(InvoicePoAllocation.invoice_id)
        .join(Invoice, Invoice.id == InvoicePoAllocation.invoice_id)
        .where(
            InvoicePoAllocation.po_id == po_id,
            Invoice.status == "matched",
        ).limit(1)
    )).scalar_one_or_none()
    if allocated is None:
        return False
    own_gr = (await db.execute(
        select(GoodsReceipt.id).where(GoodsReceipt.po_id == po_id).limit(1)
    )).scalar_one_or_none()
    return own_gr is not None
