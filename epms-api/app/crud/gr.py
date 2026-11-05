"""CRUD + workflow for Goods Receipt (GR)."""
import base64
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ontology import scope_gr
from app.crud._list_order import lower_text, sort_clauses
from app.crud._numbering import next_number
from app.crud.pr_owner import get_pr_owner_id
from app.crud.signatories import gr_signatories, resolve_user_names
from app.models.config import CompanyConfig
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.gr_attachment import GrAttachment
from app.models.invoice import Invoice
from app.models.invoice_allocation import InvoicePoAllocation
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.schemas.gr import DAMAGE_RESOLUTIONS, GrCreate, GrActionRequest, is_physical
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


def _effective_qty(line) -> Decimal:
    """The quantity a GR line posts to its PO line — actual_qty when set."""
    return line.actual_qty if line.actual_qty is not None else line.qty_received


# ── Over-receipt (ISS-0098 / ISS-0169) ─────────────────────────────────────────
#
# Receiving more than a PO line still has open is allowed, but only with a
# reason. "Open" counts GRs that are created but not yet collected/confirmed:
# their quantities reach the PO line only on collection (GR-P-003), so leaving
# them out let two GRs each take the full remainder.

#: GR statuses whose quantities are on their way to the PO line but not posted yet.
PENDING_GR_STATUSES = ("pending_ack", "collection_pending")

OVER_RECEIPT_PREFIX = "Over-receipt"


class OverReceiptReasonRequired(ValueError):
    """A receipt takes a PO line past its ordered quantity and no reason was given."""


async def open_qty_by_po_line(
    db: AsyncSession, po_id: uuid.UUID, *, exclude_gr_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, dict[str, Decimal]]:
    """po_line_id → ordered / received / pending / open for every line of the PO.

    ``open`` may be negative when the line is already over-received; callers
    that display it clamp at 0, the over-receipt check compares the raw value.
    """
    po_lines = (await db.execute(
        select(PoLineItem).where(PoLineItem.po_id == po_id))).scalars().all()
    stmt = (
        select(GrLineItem.po_line_id,
               func.sum(func.coalesce(GrLineItem.actual_qty, GrLineItem.qty_received)))
        .join(GoodsReceipt, GoodsReceipt.id == GrLineItem.gr_id)
        .where(GoodsReceipt.po_id == po_id,
               GoodsReceipt.status.in_(PENDING_GR_STATUSES),
               GrLineItem.po_line_id.is_not(None))
        .group_by(GrLineItem.po_line_id)
    )
    if exclude_gr_id is not None:
        stmt = stmt.where(GoodsReceipt.id != exclude_gr_id)
    pending = {row[0]: row[1] or Decimal("0") for row in (await db.execute(stmt)).all()}
    out: dict[uuid.UUID, dict[str, Decimal]] = {}
    for pl in po_lines:
        p = pending.get(pl.id, Decimal("0"))
        out[pl.id] = {
            "ordered": pl.qty,
            "received": pl.received_qty,
            "pending": p,
            "open": pl.qty - pl.received_qty - p,
            "description": pl.description,
        }
    return out


def _fmt_qty(q: Decimal) -> str:
    return f"{q.normalize():f}"


def _over_receipt_message(over: list[tuple[str, Decimal, Decimal]]) -> str:
    parts = "; ".join(
        f"{desc}: receiving {_fmt_qty(qty)}, only {_fmt_qty(max(avail, Decimal('0')))} still open"
        for desc, qty, avail in over
    )
    return (f"{OVER_RECEIPT_PREFIX}: {parts}. "
            "Give a reason to receive more than the PO line has open.")


async def _over_receipt_on_create(
    db: AsyncSession, po_id: uuid.UUID, items_in,
) -> list[tuple[str, Decimal, Decimal]]:
    """Lines of a new GR that exceed what their PO line has open (received + pending)."""
    wanted: dict[uuid.UUID, Decimal] = {}
    for item in items_in:
        if item.po_line_id is not None:
            wanted[item.po_line_id] = wanted.get(item.po_line_id, Decimal("0")) + _effective_qty(item)
    if not wanted:
        return []
    avail = await open_qty_by_po_line(db, po_id)
    return [
        (avail[pl]["description"], qty, avail[pl]["open"])
        for pl, qty in wanted.items()
        if pl in avail and qty > 0 and qty > avail[pl]["open"]
    ]


async def _require_over_receipt_reason_on_post(
    db: AsyncSession, gr: GoodsReceipt, reason_in: str | None,
) -> None:
    """Before a GR's quantities are posted: if they take a PO line past its
    ordered quantity and the GR carries no reason yet, one must be given now.

    Catches GRs created before the check existed and a GR collected after
    another one already filled the line.
    """
    if gr.over_receipt_reason:
        return
    posting: dict[uuid.UUID, Decimal] = {}
    for line in gr.line_items:
        if line.po_line_id is not None:
            posting[line.po_line_id] = posting.get(line.po_line_id, Decimal("0")) + _effective_qty(line)
    if not posting:
        return
    po_lines = (await db.execute(
        select(PoLineItem).where(PoLineItem.id.in_(posting.keys())))).scalars().all()
    over = [
        (pl.description, posting[pl.id], pl.qty - pl.received_qty)
        for pl in po_lines
        if posting[pl.id] > 0 and pl.received_qty + posting[pl.id] > pl.qty
    ]
    if not over:
        return
    reason = (reason_in or "").strip()
    if not reason:
        raise OverReceiptReasonRequired(_over_receipt_message(over))
    gr.over_receipt_reason = reason


# ── Reads ──────────────────────────────────────────────────────────────────────

# GET /gr ?sort= keys → what the GR list's column shows (whitelist = the
# SQL-injection boundary; unknown key → 422).
SORTABLE = {
    "number": lower_text(GoodsReceipt.number),
    "po_number": lower_text(GoodsReceipt.po_number),
    "vendor_name": lower_text(GoodsReceipt.vendor_name),
    # Labels Physical / Service sort the same as the codes.
    "gr_type": GoodsReceipt.gr_type,
    # Status labels (Cancelled, Collected, Collection Pending, Confirmed,
    # Discrepancy, Pending Acknowledgement) sort the same as the codes.
    "status": GoodsReceipt.status,
    "received_at": GoodsReceipt.received_at,
    # "Total Value" is the sum of the receipt's line totals, as the row adds it up.
    "total_value": func.coalesce(
        select(func.sum(GrLineItem.line_total))
        .where(GrLineItem.gr_id == GoodsReceipt.id)
        .correlate_except(GrLineItem)
        .scalar_subquery(),
        0,
    ),
    "received_by": lower_text(GoodsReceipt.received_by),
}
SORT_TIEBREAK = (GoodsReceipt.id.asc(),)


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
    sort: str | None = None,
    dir: str = "asc",
) -> tuple[list[GoodsReceipt], int]:
    order = sort_clauses(sort, dir, SORTABLE, SORT_TIEBREAK) or [GoodsReceipt.created_at.desc()]
    q = select(GoodsReceipt)
    # Row scope lives in ontology.scope_gr — see the note in crud/pr.py.
    q = await scope_gr(q, {"po_subq": po_ids_subq}, db)
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
        q.order_by(*order).offset(offset).limit(page_size)
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
    # One GR at a time per PO. A double-click on Submit sent two requests 1 ms
    # apart; both read the same open quantity before either was saved, so the
    # over-receipt check passed twice and the PO line was received twice
    # (GR-20261027-0004/0005, ISS-0152). Locked, the second sees the first
    # GR's quantity as pending and needs an over-receipt reason like any other.
    await db.execute(
        select(PurchaseOrder.id).where(PurchaseOrder.id == po.id).with_for_update())
    over = await _over_receipt_on_create(db, po.id, payload.line_items)
    over_reason = (payload.over_receipt_reason or "").strip() or None
    if over and over_reason is None:
        raise OverReceiptReasonRequired(_over_receipt_message(over))

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
        over_receipt_reason=over_reason if over else None,
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

    # Create the acknowledge task for the PR's owner (_create_ack_task resolves
    # it — the requester unless a service/project PR names someone else).
    # Imported POs (PMS/NC) have no linked PR → nobody to assign; a
    # requester-role task would then fall back to a role-wide broadcast
    # (2026-08-05: 59 people emailed twice). Instead the requester steps are
    # skipped entirely and system admins get an alert. The probe stays on the
    # requester: it is asking "is there a PR at all", and owner is NULL on
    # nearly every row.
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
        requester_id = await _get_pr_requester_id(db, gr)
        if requester_id is None:
            # Legacy no-PR GR (created before requester-less POs skipped the ack
            # step): there is nobody to collect/confirm — finish the chain now
            # instead of creating a requester-role broadcast task.
            gr.status = "collected" if gr.gr_type == "physical" else "confirmed"
            gr.collected_at = now
            gr.collected_by = req.acknowledged_by or await _actor_name(db, actor_id)
            await _require_over_receipt_reason_on_post(db, gr, req.over_receipt_reason)
            await _update_po_received_qty(db, gr)
            await _after_receipt_posted(db, gr)
        elif gr.gr_type == "physical":
            gr.status = "collection_pending"
            await _create_collect_task(db, gr)
        else:
            # Service GR → goes straight to confirmation step
            gr.status = "collection_pending"
            await _create_service_confirm_task(db, gr)
        # After the status moves on: the PDF footer prints the GR's status
        # (ISS-0184). Best-effort: acknowledging must not fail because the file
        # server is down — fall back to inline DB storage for the PDF.
        try:
            await _attach_gr_pdf(db, gr, company_name, token=token, cfg=cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("GR %s acknowledge: PDF upload failed (%s); storing inline", gr.number, exc)
            await _attach_gr_pdf(db, gr, company_name, token=None, cfg=cfg)

    elif act == "collect":
        if gr.status != "collection_pending" or gr.gr_type != "physical":
            raise ValueError(f"Cannot collect GR in status '{gr.status}' (type={gr.gr_type})")
        await _require_over_receipt_reason_on_post(db, gr, req.over_receipt_reason)
        await _complete_tasks(db, gr.id)
        gr.status = "collected"
        gr.collected_at = now
        gr.collected_by = req.collected_by or await _actor_name(db, actor_id)
        gr.collection_notes = req.collection_notes
        await _refresh_gr_pdf(db, gr, token=token)
        await _update_po_received_qty(db, gr)
        await _after_receipt_posted(db, gr)

    elif act == "confirm":
        allowed = {"collected", "collection_pending"}
        if gr.gr_type == "service":
            # Service GR created by requester: allow confirming directly from pending_ack
            # (collapses acknowledge + confirm into one step)
            allowed.add("pending_ack")
        if gr.status not in allowed:
            raise ValueError(f"Cannot confirm GR in status '{gr.status}'")
        await _require_over_receipt_reason_on_post(db, gr, req.over_receipt_reason)
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
        await _after_receipt_posted(db, gr)
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
        # A cancelled GR records no receipt, so there is no damage left to settle.
        await _complete_tasks(db, gr.id, include_damage_report=True)
        gr.status = "cancelled"

    elif act == "resolve_damage":
        await _resolve_damage(db, gr, req, actor_id)

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


async def _get_pr_owner_id(db: AsyncSession, gr: GoodsReceipt) -> uuid.UUID | None:
    """Who this GR's requester-side steps are assigned to.

    The OWNER of the linked PR (crud.pr_owner: owner_id, else created_by), so
    the acknowledge → collect / confirm chain lands on the same person the
    completion-date sweep and the invoice-matched nudge chase. An owner is only
    ever collected for service/project PRs, so for physical receipts this is
    the requester — identical to what these tasks carried before.

    None under exactly the same condition as _get_pr_requester_id (no linked
    PR), which is what _auto_complete_requester_steps branches on.
    """
    return await get_pr_owner_id(db, gr.pr_id)


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
    assignee_id = await _get_pr_owner_id(db, gr)
    db.add(Task(
        type="acknowledge_gr",
        priority="normal",
        document_type="gr",
        document_id=gr.id,
        document_number=gr.number,
        assigned_role="requester",
        assigned_user_id=assignee_id,
        # A service GR is acknowledged with Confirm Service Completion (there is
        # no Acknowledge button on it), so its task says so (ISS-0179).
        title=(f"Confirm Service Completion: {gr.number} — {gr.title}"
               if gr.gr_type == "service" else f"Acknowledge GR: {gr.number} — {gr.title}"),
        description=("Confirm on the goods receipt whether the service was completed "
                     "(Satisfactory or Has Issues)." if gr.gr_type == "service"
                     else "New goods receipt requires your acknowledgement."),
        vendor=gr.vendor_name,
    ))


async def _create_collect_task(db: AsyncSession, gr: GoodsReceipt) -> None:
    assignee_id = await _get_pr_owner_id(db, gr)
    db.add(Task(
        type="collect_goods",
        priority="normal",
        document_type="gr",
        document_id=gr.id,
        document_number=gr.number,
        assigned_role="requester",
        assigned_user_id=assignee_id,
        title=f"Collect Goods: {gr.number} — {gr.title}",
        description=f"Goods are ready for collection at: {gr.storage_location or 'Warehouse'}.",
        vendor=gr.vendor_name,
    ))


async def _create_service_confirm_task(db: AsyncSession, gr: GoodsReceipt) -> None:
    assignee_id = await _get_pr_owner_id(db, gr)
    db.add(Task(
        type="confirm_service_gr",
        priority="normal",
        document_type="gr",
        document_id=gr.id,
        document_number=gr.number,
        assigned_role="requester",
        assigned_user_id=assignee_id,
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


async def _resolve_damage(
    db: AsyncSession, gr: GoodsReceipt, req: GrActionRequest, actor_id: uuid.UUID,
) -> None:
    """Record the return or credit arranged with the vendor and close the
    gr_damage_report task (ISS-0099: the task closes on this, not on Collect).

    EPMS has no return document and a vendor credit is not tied to one GR, so
    what is recorded is the arrangement and the vendor's reference for it.
    """
    if gr.status == "cancelled":
        raise ValueError("Cannot record a vendor resolution on a cancelled GR")
    if not any(line.condition != "good" for line in gr.line_items):
        raise ValueError("This GR has no damaged or discrepancy lines")
    if gr.damage_resolution:
        raise ValueError("A vendor resolution is already recorded on this GR")
    if req.damage_resolution not in DAMAGE_RESOLUTIONS:
        raise ValueError(f"damage_resolution must be one of {', '.join(DAMAGE_RESOLUTIONS)}")
    ref = (req.damage_resolution_ref or "").strip()
    if not ref:
        raise ValueError("Enter the return or credit note reference")
    gr.damage_resolution = req.damage_resolution
    gr.damage_resolution_ref = ref
    gr.damage_resolution_note = (req.damage_resolution_note or "").strip() or None
    gr.damage_resolved_at = datetime.now(timezone.utc)
    gr.damage_resolved_by = await _actor_name(db, actor_id)
    tasks = (await db.execute(select(Task).where(
        Task.type == "gr_damage_report",
        Task.document_type == "gr",
        Task.document_id == gr.id,
        Task.is_completed.is_(False),
    ))).scalars().all()
    for task in tasks:
        task.is_completed = True
        task.completed_at = gr.damage_resolved_at
        task.completed_by = actor_id


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
        # Zero-allocation header link: a PMS/NC-imported invoice is matched in
        # bulk straight onto Invoice.po_id and never gets an
        # invoice_po_allocations row at all (419 of them in production). Both
        # queries above start FROM that table, so those invoices were invisible
        # here — the GR never linked, invoice.gr_id stayed NULL forever, and
        # with it po_has_three_way_matched_invoice stayed False, so the PO never
        # got its create_pa task (real prod: the goods were received and
        # invoiced, yet nothing ever asked anyone to pay). The whole invoice
        # bills this PO, so — exactly like the header-level branch above — any
        # GR on the PO is its receipt evidence.
        inv_ids += (await db.execute(
            select(Invoice.id).where(
                Invoice.po_id == gr.po_id,
                ~select(InvoicePoAllocation.id)
                .where(InvoicePoAllocation.invoice_id == Invoice.id)
                .exists(),
            )
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

    # The session runs autoflush=False (app/db/session.py), so these writes stay
    # invisible to any later raw SELECT until something flushes them. The very
    # next caller IS such a SELECT: _on_three_way_reached →
    # po_has_three_way_matched_invoice reads Invoice.gr_id to decide whether the
    # PO reached 3-way. Without this flush it read a stale NULL and skipped the
    # create_pa task — that is what happened to prod PO-192-2608-01 on
    # 2026-08-07 (confirm_receipt closed, create_pa never raised). The
    # allocation branch added later to po_has_three_way_matched_invoice happens
    # to mask it today; the missing flush is the actual defect.
    await db.flush()


async def _after_receipt_posted(db: AsyncSession, gr: GoodsReceipt) -> None:
    """A GR was collected / confirmed: with the collection gate on, this is
    when the PO becomes ready for a PA, so raise create_pa now (idempotent —
    nothing happens if it already exists or the PO has a PA). Flushed first:
    the session does not autoflush and the readiness check is a SELECT on the
    quantities just posted."""
    if gr.po_id is None:
        return
    await db.flush()
    await _on_three_way_reached(db, gr.po_id)


async def _on_three_way_reached(db: AsyncSession, po_id: uuid.UUID) -> None:
    """GR 创建(或领取)使 PO 达成 3-way 后:关掉催收货提醒,补建 create_pa(若尚无 PA/任务)。

    With the collection gate on (ISS-0146) create_pa waits until the goods are
    collected, so this also runs after collect / confirm."""
    from app.crud.po import po_ready_for_pa
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
    # 2) 若已 3-way(且收货闸门要求时已领取)且无 PA 且无 open create_pa → 建 create_pa
    po_row = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one_or_none()
    if po_row is None or not await po_ready_for_pa(db, po_row):
        return
    from app.crud.pa_links import pa_ids_for_po
    has_pa = (await db.execute(
        pa_ids_for_po(po_id).limit(1))).scalar_one_or_none()
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


async def _refresh_gr_pdf(
    db: AsyncSession, gr: GoodsReceipt, token: str | None = None,
) -> None:
    """Regenerate the GR PDF on Collect so it carries Collected By (GR-P-007,
    ISS-0081). Best-effort, like acknowledge and confirm: collecting must not
    fail because the file server is down — fall back to inline DB storage."""
    cfg = await _get_config(db)
    company_name = cfg.name if cfg else "EPMS"
    try:
        await _attach_gr_pdf(db, gr, company_name, token=token, cfg=cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GR %s collect: PDF upload failed (%s); storing inline", gr.number, exc)
        await _attach_gr_pdf(db, gr, company_name, token=None, cfg=cfg)


async def _complete_tasks(
    db: AsyncSession, gr_id: uuid.UUID, *, include_damage_report: bool = False,
) -> None:
    """Close the GR's open step tasks.

    The gr_damage_report task is left open unless asked for: it is not a step
    of the receipt flow, and stays open until Procurement records a return or
    credit (resolve_damage) or the GR is cancelled (ISS-0099).
    """
    conds = [
        Task.document_type == "gr",
        Task.document_id == gr_id,
        Task.is_completed.is_(False),
    ]
    if not include_damage_report:
        conds.append(Task.type != "gr_damage_report")
    result = await db.execute(select(Task).where(*conds))
    now = datetime.now(timezone.utc)
    for task in result.scalars().all():
        task.is_completed = True
        task.completed_at = now
