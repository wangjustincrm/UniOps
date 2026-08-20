"""CRUD + workflow for Goods Receipt (GR)."""
import base64
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._numbering import next_number
from app.crud.signatories import gr_signatories, resolve_user_names
from app.models.config import CompanyConfig
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.gr_attachment import GrAttachment
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.schemas.gr import GrCreate, GrActionRequest, is_physical
from app.services.pdf_gr import generate_gr_pdf

logger = logging.getLogger(__name__)


# ── Number generation ──────────────────────────────────────────────────────────

async def _next_number(db: AsyncSession) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"GR-{today}-"
    return await next_number(db, GoodsReceipt.number, prefix, width=4)


async def _actor_name(db: AsyncSession, actor_id: uuid.UUID) -> str:
    """Display name for the acting user, falling back to the raw id if unknown.

    The browser sends the display name in the request body; NC/PMS imports and
    Teams actions do not. Storing a bare UUID in these name columns is what used
    to print an id on the GR PDF.
    """
    return (await resolve_user_names(db, [actor_id])).get(actor_id) or str(actor_id)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_line_items(gr_id: uuid.UUID, items_in) -> list[GrLineItem]:
    return [
        GrLineItem(
            gr_id=gr_id,
            po_line_id=item.po_line_id,
            description=item.description,
            material_id=item.material_id,
            qty_ordered=item.qty_ordered,
            qty_received=item.qty_received,
            unit=item.unit,
            unit_price=item.unit_price,
            line_total=item.line_total,
            condition=item.condition,
            discrepancy_notes=item.discrepancy_notes,
            actual_qty=item.actual_qty,
            sort_order=i,
        )
        for i, item in enumerate(items_in)
    ]


# ── Reads ──────────────────────────────────────────────────────────────────────

async def get_all(
    db: AsyncSession,
    *,
    status: str | None = None,
    po_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    search: str | None = None,
    gr_type: str | None = None,
    po_ids_subq=None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[GoodsReceipt], int]:
    q = select(GoodsReceipt)
    if po_ids_subq is not None:
        q = q.where(GoodsReceipt.po_id.in_(po_ids_subq))
    if status:
        q = q.where(GoodsReceipt.status == status)
    if po_id:
        q = q.where(GoodsReceipt.po_id == po_id)
    if vendor_id:
        q = q.where(GoodsReceipt.vendor_id == vendor_id)
    if created_by:
        q = q.where(GoodsReceipt.created_by == created_by)
    if gr_type:
        q = q.where(GoodsReceipt.gr_type == gr_type)
    if search:
        term = f"%{search}%"
        # Match what the UI advertises: GR#, PO#, vendor name.
        q = q.where(
            GoodsReceipt.number.ilike(term)
            | GoodsReceipt.po_number.ilike(term)
            | GoodsReceipt.vendor_name.ilike(term)
        )
    total: int = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    offset = (page - 1) * page_size
    items = list((await db.execute(
        q.order_by(GoodsReceipt.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all())
    return items, total


async def get_by_id(db: AsyncSession, gr_id: uuid.UUID) -> GoodsReceipt | None:
    result = await db.execute(select(GoodsReceipt).where(GoodsReceipt.id == gr_id))
    return result.scalar_one_or_none()


# ── Create ─────────────────────────────────────────────────────────────────────

async def create(
    db: AsyncSession,
    payload: GrCreate,
    po: PurchaseOrder,
    created_by: uuid.UUID,
    token: str | None = None,
) -> GoodsReceipt:
    number = await _next_number(db)
    gr_type = "physical" if is_physical(po.type) else "service"
    received_by_name = payload.received_by or await _actor_name(db, created_by)

    gr = GoodsReceipt(
        number=number,
        title=payload.title,
        po_id=po.id,
        po_number=po.number,
        pr_id=po.pr_id,
        pr_number=po.pr_number,
        vendor_id=po.vendor_id,
        vendor_name=po.vendor_name,
        gr_type=gr_type,
        procurement_type=po.type,
        currency=payload.currency,
        status="pending_ack",
        storage_location=payload.storage_location,
        notes=payload.notes,
        received_at=datetime.now(timezone.utc),
        received_by=received_by_name,
        created_by=created_by,
    )
    db.add(gr)
    await db.flush()

    lines = _build_line_items(gr.id, payload.line_items)
    for item in lines:
        db.add(item)

    # Save uploaded pack list attachments to file server
    for att in payload.attachments:
        try:
            file_bytes = base64.b64decode(att.data)
        except Exception:
            continue
        if token:
            from app.services.attachment_helper import upload_to_file_server
            storage_key = await upload_to_file_server(
                file_bytes, att.filename, att.content_type, "gr", gr.id, token,
            )
            db.add(GrAttachment(
                gr_id=gr.id, filename=att.filename,
                content_type=att.content_type, file_size=len(file_bytes),
                storage_key=storage_key,
            ))
        else:
            db.add(GrAttachment(
                gr_id=gr.id, filename=att.filename,
                content_type=att.content_type, file_size=len(file_bytes),
                file_data=file_bytes,
            ))

    # Create acknowledge task for the PR requester. Imported POs (PMS/NC) have no
    # linked PR → no requester exists; a requester-role task would then fall back
    # to a role-wide broadcast (2026-08-05: 59 people emailed twice). Instead the
    # requester steps are skipped entirely and system admins get an alert.
    requester_id = await get_pr_requester_id(db, po.pr_id)
    if requester_id is not None:
        await _create_ack_task(db, gr)
    else:
        await _auto_complete_requester_steps(
            db, gr, actor_id=created_by, lines=lines, token=token,
        )

    # Notify Procurement Officer if any line arrived damaged or with discrepancy
    damaged_lines = [item for item in payload.line_items if item.condition not in ("good", None)]
    if damaged_lines:
        await _create_damage_report_task(db, gr, damaged_lines)

    await db.flush()
    # Line-item reverse-match: link this GR to any already-matched invoice billing
    # the same PO lines, and refresh its gr_value (convenience — no status change).
    await _autofill_gr_to_matched_invoices(db, gr, lines)
    if gr.po_id is not None:
        await _on_three_way_reached(db, gr.po_id)
    await db.refresh(gr)
    return gr


# ── Actions ────────────────────────────────────────────────────────────────────

async def _get_config(db: AsyncSession) -> CompanyConfig | None:
    result = await db.execute(select(CompanyConfig).limit(1))
    return result.scalar_one_or_none()


async def action(
    db: AsyncSession,
    gr: GoodsReceipt,
    req: GrActionRequest,
    actor_id: uuid.UUID,
    token: str | None = None,
) -> GoodsReceipt:
    act = req.action.lower()
    now = datetime.now(timezone.utc)
    cfg = await _get_config(db)
    company_name = cfg.name if cfg else "EPMS"

    if act == "acknowledge":
        if gr.status != "pending_ack":
            raise ValueError(f"Cannot acknowledge GR in status '{gr.status}'")
        await _complete_tasks(db, gr.id)
        gr.acknowledged_at = now
        gr.acknowledged_by = req.acknowledged_by or await _actor_name(db, actor_id)
        # Best-effort: acknowledging must not fail because the file server is
        # down — fall back to inline DB storage for the PDF.
        try:
            await _attach_gr_pdf(db, gr, company_name, token=token, cfg=cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("GR %s acknowledge: PDF upload failed (%s); storing inline", gr.number, exc)
            await _attach_gr_pdf(db, gr, company_name, token=None, cfg=cfg)
        requester_id = await _get_pr_requester_id(db, gr)
        if requester_id is None:
            # Legacy no-PR GR (created before requester-less POs skipped the ack
            # step): there is nobody to collect/confirm — finish the chain now
            # instead of creating a requester-role broadcast task.
            gr.status = "collected" if gr.gr_type == "physical" else "confirmed"
            gr.collected_at = now
            gr.collected_by = req.acknowledged_by or await _actor_name(db, actor_id)
            await _update_po_received_qty(db, gr)
        elif gr.gr_type == "physical":
            gr.status = "collection_pending"
            await _create_collect_task(db, gr)
        else:
            # Service GR → goes straight to confirmation step
            gr.status = "collection_pending"
            await _create_service_confirm_task(db, gr)

    elif act == "collect":
        if gr.status != "collection_pending" or gr.gr_type != "physical":
            raise ValueError(f"Cannot collect GR in status '{gr.status}' (type={gr.gr_type})")
        await _complete_tasks(db, gr.id)
        gr.status = "collected"
        gr.collected_at = now
        gr.collected_by = req.collected_by or await _actor_name(db, actor_id)
        gr.collection_notes = req.collection_notes
        await _update_po_received_qty(db, gr)

    elif act == "confirm":
        allowed = {"collected", "collection_pending"}
        if gr.gr_type == "service":
            # Service GR created by requester: allow confirming directly from pending_ack
            # (collapses acknowledge + confirm into one step)
            allowed.add("pending_ack")
        if gr.status not in allowed:
            raise ValueError(f"Cannot confirm GR in status '{gr.status}'")
        await _complete_tasks(db, gr.id)
        if gr.status == "pending_ack":
            gr.acknowledged_at = now
            gr.acknowledged_by = req.collected_by or await _actor_name(db, actor_id)
        gr.status = "confirmed"
        gr.collected_at = now
        gr.collected_by = req.collected_by or await _actor_name(db, actor_id)
        gr.collection_notes = req.collection_notes
        # Update PO line received_qty and PO status
        await _update_po_received_qty(db, gr)
        # Best-effort: confirming must not fail because the file server is down —
        # fall back to inline DB storage for the PDF. Service GRs collapse
        # acknowledge + confirm into this one step and would otherwise never get
        # a PDF; physical GRs already have one from acknowledge, and
        # _attach_gr_pdf replaces it so this ends with exactly one, now carrying
        # Collected By (must run after gr.collected_by is assigned above).
        try:
            await _attach_gr_pdf(db, gr, company_name, token=token, cfg=cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("GR %s confirm: PDF upload failed (%s); storing inline", gr.number, exc)
            await _attach_gr_pdf(db, gr, company_name, token=None, cfg=cfg)

    elif act == "reject":
        if gr.status != "collection_pending" or gr.gr_type != "service":
            raise ValueError(f"Cannot reject GR in status '{gr.status}' (only service GRs in collection_pending)")
        await _complete_tasks(db, gr.id)
        gr.status = "rejected"
        gr.collection_notes = req.collection_notes

    elif act == "discrepancy":
        if gr.status != "collected":
            raise ValueError(f"Cannot mark discrepancy on GR in status '{gr.status}'")
        await _complete_tasks(db, gr.id)
        gr.status = "discrepancy"

    elif act == "cancel":
        if gr.status not in ("pending_ack", "collection_pending"):
            raise ValueError(f"Cannot cancel GR in status '{gr.status}'")
        await _complete_tasks(db, gr.id)
        gr.status = "cancelled"

    else:
        raise ValueError(f"Unknown action '{act}'")

    await db.flush()
    await db.refresh(gr)
    return gr


# ── PO received_qty sync ───────────────────────────────────────────────────────

#: GR statuses whose quantities are already counted in ``po_line_items.received_qty``.
#: These are exactly the statuses reachable through a call to
#: ``_update_po_received_qty`` — ``collected`` and ``confirmed`` from the collect /
#: confirm / auto-complete paths, plus ``discrepancy``, which is only reachable
#: *from* ``collected`` and so inherits an already-counted quantity. Everything
#: else (``pending_ack``, ``collection_pending``, ``cancelled``, ``rejected``)
#: never contributed and must not be given back.
COUNTED_GR_STATUSES = ("collected", "confirmed", "discrepancy")


def _po_receipt_status(po_status: str, lines) -> str | None:
    """The receipt status these lines imply, or None to leave the PO's status alone.

    Only the three receipt-tracking statuses are ours to move; ``draft`` /
    ``approved`` / ``closed`` / ``cancelled`` / ``nc_milk`` mean something a
    quantity change has no business overriding.
    """
    if po_status not in ("issued", "partially_received", "fully_received") or not lines:
        return None
    if all(line.received_qty >= line.qty for line in lines):
        return "fully_received"
    if any(line.received_qty > 0 for line in lines):
        return "partially_received"
    return "issued"


async def sync_po_receipt_status(db: AsyncSession, po_id: uuid.UUID | None) -> None:
    """Point the PO's status at what its line quantities now say — in both directions.

    Receipts can be taken away as well as recorded (Data Maintenance deletes a GR,
    an admin corrects a received_qty by hand), so a PO that no longer has all its
    goods must be able to fall back out of ``fully_received``.
    """
    if po_id is None:
        return
    po = (await db.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one_or_none()
    if po is None:
        return
    lines = (await db.execute(
        select(PoLineItem).where(PoLineItem.po_id == po.id))).scalars().all()
    new_status = _po_receipt_status(po.status, lines)
    if new_status is not None:
        po.status = new_status


async def resync_po_received_qty(
    db: AsyncSession, po_id: uuid.UUID | None, *,
    exclude_gr_ids: "tuple[uuid.UUID, ...] | set[uuid.UUID]" = (),
) -> int:
    """Recompute every PO line's ``received_qty`` from the GRs that still count.

    ``_update_po_received_qty`` only ever adds, so any path that takes a receipt
    away — deleting a GR, flipping one to cancelled — would otherwise leave the PO
    claiming goods it does not have: the outstanding quantity a follow-up GR needs
    is eaten, and the PO is pinned at ``fully_received`` forever.

    This recomputes from the surviving GR lines rather than subtracting the
    departing one. Subtraction is only correct if every historical addition was;
    a recompute is idempotent and self-healing, so a PO that has already drifted
    is repaired the next time anything touches it. It also stays consistent with
    the NC mirror, whose ``received_qty`` is by construction the same sum of the
    same arrival quantities.

    Returns the number of PO lines whose value actually moved.
    """
    if po_id is None:
        return 0
    po_lines = (await db.execute(
        select(PoLineItem).where(PoLineItem.po_id == po_id))).scalars().all()
    if not po_lines:
        return 0

    stmt = (
        select(GrLineItem.po_line_id,
               func.sum(func.coalesce(GrLineItem.actual_qty, GrLineItem.qty_received)))
        .join(GoodsReceipt, GoodsReceipt.id == GrLineItem.gr_id)
        .where(GoodsReceipt.po_id == po_id,
               GoodsReceipt.status.in_(COUNTED_GR_STATUSES),
               GrLineItem.po_line_id.is_not(None))
        .group_by(GrLineItem.po_line_id)
    )
    if exclude_gr_ids:
        stmt = stmt.where(GoodsReceipt.id.not_in(tuple(exclude_gr_ids)))
    totals = {row[0]: row[1] or Decimal("0") for row in (await db.execute(stmt)).all()}

    changed = 0
    for po_line in po_lines:
        fresh = totals.get(po_line.id, Decimal("0"))
        if po_line.received_qty != fresh:
            po_line.received_qty = fresh
            changed += 1

    await sync_po_receipt_status(db, po_id)
    return changed


async def _update_po_received_qty(
    db: AsyncSession, gr: GoodsReceipt, lines: list[GrLineItem] | None = None,
) -> None:
    """Add confirmed GR quantities to PO line received_qty, then update PO receipt status.

    ``lines`` lets callers that already hold the (possibly not-yet-loaded) GR line
    objects pass them explicitly instead of relying on the relationship."""
    for gr_line in (lines if lines is not None else gr.line_items):
        if gr_line.po_line_id is None:
            continue
        result = await db.execute(
            select(PoLineItem).where(PoLineItem.id == gr_line.po_line_id)
        )
        po_line = result.scalar_one_or_none()
        if po_line:
            effective_qty = gr_line.actual_qty if gr_line.actual_qty is not None else gr_line.qty_received
            po_line.received_qty = po_line.received_qty + effective_qty

    await sync_po_receipt_status(db, gr.po_id)


# ── Task helpers ───────────────────────────────────────────────────────────────

async def get_pr_requester_id(db: AsyncSession, pr_id: uuid.UUID | None) -> uuid.UUID | None:
    """Return the created_by (requester) of the given PR, or None if not found.

    "Requester" throughout the GR flow means the person who raised the originating
    PR — NOT the PO creator (POs are created by procurement staff). Used both for
    routing acknowledge/confirm tasks and for authorizing who may create a GR.
    """
    if not pr_id:
        return None
    result = await db.execute(
        select(PurchaseRequest.created_by).where(PurchaseRequest.id == pr_id)
    )
    return result.scalar_one_or_none()


async def _get_pr_requester_id(db: AsyncSession, gr: GoodsReceipt) -> uuid.UUID | None:
    """Return the created_by (requester) of the PR linked to this GR, or None if not found."""
    return await get_pr_requester_id(db, gr.pr_id)


async def _auto_complete_requester_steps(
    db: AsyncSession,
    gr: GoodsReceipt,
    *,
    actor_id: uuid.UUID,
    lines: list[GrLineItem] | None = None,
    token: str | None = None,
) -> None:
    """Skip the requester chain (acknowledge → collect/confirm) when the GR's PO
    has no PR requester (imported POs). The warehouse/procurement user creating
    the GR has already received the goods, so the GR jumps straight to its
    terminal status; system admins are alerted so the missing PR link gets fixed.
    """
    now = datetime.now(timezone.utc)
    gr.acknowledged_at = now
    gr.acknowledged_by = "auto (no PR requester)"
    gr.collected_at = now
    gr.collected_by = await _actor_name(db, actor_id)
    gr.status = "collected" if gr.gr_type == "physical" else "confirmed"

    cfg = await _get_config(db)
    company_name = cfg.name if cfg else "EPMS"
    await db.flush()
    await db.refresh(gr)   # load line_items for the PDF renderer (runs sync)
    # PDF is best-effort here — GR creation must not fail because the file
    # server is down; fall back to inline DB storage, then give up quietly.
    try:
        await _attach_gr_pdf(db, gr, company_name, token=token, cfg=cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GR %s auto-complete: PDF upload failed (%s); storing inline", gr.number, exc)
        try:
            await _attach_gr_pdf(db, gr, company_name, token=None, cfg=cfg)
        except Exception as exc2:  # noqa: BLE001
            logger.error("GR %s auto-complete: inline PDF attach failed too: %s", gr.number, exc2)
    await _update_po_received_qty(db, gr, lines=lines)

    from app.services import notification as notification_service
    notification_service.fire_and_forget_admin_alert(
        f"[EPMS] GR {gr.number} auto-completed — PO {gr.po_number} has no linked PR",
        (
            f"Goods Receipt <b>{gr.number}</b> was created for PO <b>{gr.po_number}</b>, "
            f"which has no linked PR — there is no requester to acknowledge it, so the "
            f"requester steps (acknowledge/collect/confirm) were skipped and the GR was "
            f"marked <b>{gr.status}</b>.\n\n"
            f"Imported POs (PMS migration / NC sync) can lose their PR link. Please "
            f"verify this PO and restore the link if the PR exists."
        ),
    )


async def _create_ack_task(db: AsyncSession, gr: GoodsReceipt) -> None:
    requester_id = await _get_pr_requester_id(db, gr)
    db.add(Task(
        type="acknowledge_gr",
        priority="normal",
        document_type="gr",
        document_id=gr.id,
        document_number=gr.number,
        assigned_role="requester",
        assigned_user_id=requester_id,
        title=f"Acknowledge GR: {gr.number} — {gr.title}",
        description="New goods receipt requires your acknowledgement.",
        vendor=gr.vendor_name,
    ))


async def _create_collect_task(db: AsyncSession, gr: GoodsReceipt) -> None:
    requester_id = await _get_pr_requester_id(db, gr)
    db.add(Task(
        type="collect_goods",
        priority="normal",
        document_type="gr",
        document_id=gr.id,
        document_number=gr.number,
        assigned_role="requester",
        assigned_user_id=requester_id,
        title=f"Collect Goods: {gr.number} — {gr.title}",
        description=f"Goods are ready for collection at: {gr.storage_location or 'Warehouse'}.",
        vendor=gr.vendor_name,
    ))


async def _create_service_confirm_task(db: AsyncSession, gr: GoodsReceipt) -> None:
    requester_id = await _get_pr_requester_id(db, gr)
    db.add(Task(
        type="confirm_service_gr",
        priority="normal",
        document_type="gr",
        document_id=gr.id,
        document_number=gr.number,
        assigned_role="requester",
        assigned_user_id=requester_id,
        title=f"Confirm Service: {gr.number} — {gr.title}",
        description="Please confirm that the service has been delivered.",
        vendor=gr.vendor_name,
    ))


async def _create_damage_report_task(db: AsyncSession, gr: GoodsReceipt, damaged_lines: list) -> None:
    """Create a task for Procurement Officer when GR lines arrive damaged or with discrepancies."""
    line_summary = "; ".join(
        f"{item.description} ({item.condition})" for item in damaged_lines
    )
    db.add(Task(
        type="gr_damage_report",
        priority="urgent",
        document_type="gr",
        document_id=gr.id,
        document_number=gr.number,
        assigned_role="procurement_officer",
        title=f"Damaged/Discrepancy Goods: {gr.number} — {gr.title}",
        description=f"GR {gr.number} received from {gr.vendor_name} has {len(damaged_lines)} line(s) with damage or discrepancy. "
                    f"Please raise a return or credit note with the vendor. Affected: {line_summary}",
        vendor=gr.vendor_name,
    ))


async def _autofill_gr_to_matched_invoices(
    db: AsyncSession, gr: GoodsReceipt, lines: list[GrLineItem],
) -> None:
    """When a GR is created, link it to invoice(s) already matched against the
    SAME PO line items this GR covers, and refresh their gr_value.

    GR lines carry po_line_id (chosen from the PO at GR creation); a matched
    invoice's allocations (invoice_po_allocations) carry the same po_line_id — so
    we attach the GR to exactly the invoice(s) billing the lines that were
    received. This is correct when ONE PO has several GRs (each reaches only the
    invoice for its own lines, not every invoice on the PO) and when ONE invoice
    spans several GRs (each new GR appends, and gr_value re-sums across all linked
    GRs). Convenience only — match status is PO-vs-invoice variance, so this never
    re-runs matching or changes status. Idempotent per GR id. Invoices not yet
    matched (no allocations) are left alone; they pick the GR up when matched.

    PO-level fallback: an invoice matched BY TOTAL AMOUNT (the total-value panel,
    the legacy single-PO shim, and auto-match-on-upload when it cannot split by
    line) allocates against the PO header with po_line_id NULL. There is no line
    to route by, so those invoices — and only those — link at PO level instead.
    Invoices that DO allocate by line on this PO keep the precise line routing.
    """
    po_line_ids = [ln.po_line_id for ln in lines if ln.po_line_id is not None]
    inv_ids: list[uuid.UUID] = []
    if po_line_ids:
        inv_ids += (await db.execute(
            select(InvoicePoAllocation.invoice_id)
            .where(InvoicePoAllocation.po_line_id.in_(po_line_ids))
            .distinct()
        )).scalars().all()
    if gr.po_id is not None:
        # Header-level only: every allocation this invoice makes against THIS PO
        # carries a NULL po_line_id (HAVING count(po_line_id) = 0). An invoice
        # that mixes line-level allocations on this PO is excluded — it is
        # routable by line and must not be swept in wholesale.
        inv_ids += (await db.execute(
            select(InvoicePoAllocation.invoice_id)
            .where(InvoicePoAllocation.po_id == gr.po_id)
            .group_by(InvoicePoAllocation.invoice_id)
            .having(func.count(InvoicePoAllocation.po_line_id) == 0)
        )).scalars().all()
    if not inv_ids:
        return
    invoices = (await db.execute(
        select(Invoice).where(
            Invoice.id.in_(set(inv_ids)),
            # match_review = matched, pending an AP reviewer's approval. Leaving it
            # out stranded those invoices with gr_id NULL (empty 3-Way Match view,
            # blocked PA receipt gate) until someone linked the GR by hand.
            Invoice.status.in_(("matched", "match_review", "exception")),
        )
    )).scalars().all()
    for inv in invoices:
        existing = list(inv.gr_ids or [])
        if str(gr.id) in existing:
            continue  # idempotent
        existing.append(str(gr.id))
        gr_uuids = [uuid.UUID(g) for g in existing]
        # Recompute gr_value / gr_number across ALL linked GRs (match() semantics).
        num_rows = (await db.execute(
            select(GoodsReceipt.id, GoodsReceipt.number).where(GoodsReceipt.id.in_(gr_uuids))
        )).all()
        num_by_id = {r.id: r.number for r in num_rows}
        total = (await db.execute(
            select(func.coalesce(func.sum(GrLineItem.line_total), Decimal("0")))
            .where(GrLineItem.gr_id.in_(gr_uuids))
        )).scalar_one()
        inv.gr_ids = existing
        inv.gr_id = gr_uuids[0]
        inv.gr_number = ", ".join(num_by_id.get(u, "") for u in gr_uuids)
        inv.gr_value = total


async def _on_three_way_reached(db: AsyncSession, po_id: uuid.UUID) -> None:
    """GR 创建使 PO 达成 3-way 后:关掉催收货提醒,补建 create_pa(若尚无 PA/任务)。"""
    from app.crud.po import po_has_three_way_matched_invoice
    # 1) 关闭 confirm_receipt
    now = datetime.now(timezone.utc)
    rows = (await db.execute(select(Task).where(
        Task.type == "confirm_receipt",
        Task.document_type == "po",
        Task.document_id == po_id,
        Task.is_completed.is_(False),
    ))).scalars().all()
    for t in rows:
        t.is_completed = True
        t.completed_at = now
    # 2) 若已 3-way 且无 PA 且无 open create_pa → 建 create_pa
    if not await po_has_three_way_matched_invoice(db, po_id):
        return
    from app.models.pa import PaymentApplication
    has_pa = (await db.execute(select(PaymentApplication.id).where(
        PaymentApplication.po_id == po_id).limit(1))).scalar_one_or_none()
    if has_pa is not None:
        return
    has_task = (await db.execute(select(Task.id).where(
        Task.type == "create_pa", Task.document_type == "po",
        Task.document_id == po_id, Task.is_completed.is_(False)).limit(1))).scalar_one_or_none()
    if has_task is not None:
        return
    po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one_or_none()
    if po is None:
        return
    requester_id = await get_pr_requester_id(db, po.pr_id)
    # No PR → no requester; route to the ERP PA officers (the role that owns PA
    # creation for imported no-PR POs) instead of a requester-wide broadcast.
    db.add(Task(
        type="create_pa", priority="normal",
        document_type="po", document_id=po_id, document_number=po.number,
        assigned_role="requester" if requester_id else "erp_pa_officer",
        assigned_user_id=requester_id,
        title=f"Create Payment Application for {po.number}",
        description=f"Goods received for PO {po.number}. Please create a Payment Application.",
        vendor=po.vendor_name, amount=po.total,
    ))
    await db.flush()


async def _attach_gr_pdf(
    db: AsyncSession,
    gr: GoodsReceipt,
    company_name: str,
    token: str | None = None,
    cfg: CompanyConfig | None = None,
) -> None:
    """Generate a GR PDF and store it via file server (PRD §3.4).

    Idempotent: replaces any existing ``<number>.pdf`` attachment (row + backing
    file) first. This is called from both the acknowledge branch and the confirm
    branch of ``action()`` — a physical GR walks acknowledge → confirm and must
    end up with exactly one PDF (the confirm-time one, which additionally has
    Collected By filled in), not two.
    """
    import asyncio
    sig = await gr_signatories(db, gr)
    loop = asyncio.get_running_loop()
    pdf_bytes = await loop.run_in_executor(
        None, generate_gr_pdf, gr, company_name,
        cfg.pdf_templates if cfg else None,
        cfg.logo_data_url if cfg else None,
        sig["created_by_name"], sig["received_by"], sig["acknowledged_by"],
        sig["collected_by"],
    )
    filename = f"{gr.number}.pdf"

    # Replace any prior auto-PDF of the same name (row + backing file) so
    # repeated calls (acknowledge, then confirm) end with exactly one attachment.
    existing = (await db.execute(
        select(GrAttachment).where(
            GrAttachment.gr_id == gr.id,
            GrAttachment.filename == filename,
        )
    )).scalars().all()
    if existing:
        from app.services.attachment_helper import delete_from_file_server
        for att in existing:
            if att.storage_key and token:
                await delete_from_file_server(att.storage_key, token)
            await db.delete(att)
        await db.flush()

    if token:
        from app.services.attachment_helper import upload_to_file_server
        storage_key = await upload_to_file_server(
            pdf_bytes, filename, "application/pdf", "gr", gr.id, token,
        )
        db.add(GrAttachment(
            gr_id=gr.id, filename=filename,
            content_type="application/pdf", file_size=len(pdf_bytes),
            storage_key=storage_key,
        ))
    else:
        db.add(GrAttachment(
            gr_id=gr.id, filename=filename,
            content_type="application/pdf", file_size=len(pdf_bytes),
            file_data=pdf_bytes,
        ))


async def _complete_tasks(db: AsyncSession, gr_id: uuid.UUID) -> None:
    result = await db.execute(
        select(Task).where(
            Task.document_type == "gr",
            Task.document_id == gr_id,
            Task.is_completed.is_(False),
        )
    )
    now = datetime.now(timezone.utc)
    for task in result.scalars().all():
        task.is_completed = True
        task.completed_at = now
