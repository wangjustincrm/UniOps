"""CRUD + workflow for Goods Receipt (GR)."""
import base64
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import CompanyConfig
from app.models.gr import GoodsReceipt, GrLineItem
from app.models.gr_attachment import GrAttachment
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.schemas.gr import GrCreate, GrActionRequest, is_physical
from app.services.pdf_gr import generate_gr_pdf


# ── Number generation ──────────────────────────────────────────────────────────

async def _next_number(db: AsyncSession) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"GR-{today}-"
    result = await db.execute(
        select(func.count()).where(GoodsReceipt.number.like(f"{prefix}%"))
    )
    count = result.scalar_one()
    return f"{prefix}{count + 1:04d}"


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
        received_by=payload.received_by or str(created_by),
        created_by=created_by,
    )
    db.add(gr)
    await db.flush()

    for item in _build_line_items(gr.id, payload.line_items):
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

    # Create acknowledge task for the PR requester / general role
    await _create_ack_task(db, gr)

    # Notify Procurement Officer if any line arrived damaged or with discrepancy
    damaged_lines = [item for item in payload.line_items if item.condition not in ("good", None)]
    if damaged_lines:
        await _create_damage_report_task(db, gr, damaged_lines)

    await db.flush()
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
        gr.acknowledged_by = req.acknowledged_by or str(actor_id)
        await _attach_gr_pdf(db, gr, company_name, token=token, cfg=cfg)
        if gr.gr_type == "physical":
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
        gr.collected_by = req.collected_by or str(actor_id)
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
            gr.acknowledged_by = req.collected_by or str(actor_id)
        gr.status = "confirmed"
        gr.collected_at = now
        gr.collected_by = req.collected_by or str(actor_id)
        gr.collection_notes = req.collection_notes
        # Update PO line received_qty and PO status
        await _update_po_received_qty(db, gr)

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

async def _update_po_received_qty(db: AsyncSession, gr: GoodsReceipt) -> None:
    """Add confirmed GR quantities to PO line received_qty, then update PO receipt status."""
    for gr_line in gr.line_items:
        if gr_line.po_line_id is None:
            continue
        result = await db.execute(
            select(PoLineItem).where(PoLineItem.id == gr_line.po_line_id)
        )
        po_line = result.scalar_one_or_none()
        if po_line:
            effective_qty = gr_line.actual_qty if gr_line.actual_qty is not None else gr_line.qty_received
            po_line.received_qty = po_line.received_qty + effective_qty

    # Re-fetch the PO and all its lines to decide on the PO receipt status
    if gr.po_id is None:
        return
    po_result = await db.execute(
        select(PurchaseOrder).where(PurchaseOrder.id == gr.po_id)
    )
    po = po_result.scalar_one_or_none()
    if po is None or po.status not in ("issued", "partially_received", "fully_received"):
        return

    lines_result = await db.execute(
        select(PoLineItem).where(PoLineItem.po_id == po.id)
    )
    lines = lines_result.scalars().all()
    if not lines:
        return

    all_received = all(line.received_qty >= line.qty for line in lines)
    any_received = any(line.received_qty > 0 for line in lines)

    if all_received:
        po.status = "fully_received"
    elif any_received:
        po.status = "partially_received"


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


async def _create_pa_task(db: AsyncSession, gr: GoodsReceipt) -> None:
    """Create a create_pa task for the PR requester after GR is collected/confirmed."""
    requester_id = await _get_pr_requester_id(db, gr)
    # Anchor the task on the PO (not the GR) so the frontend's ?poId=task.document_id
    # navigation lands on the PO — matches the invoice-match create_pa path.
    db.add(Task(
        type="create_pa",
        priority="normal",
        document_type="po",
        document_id=gr.po_id,
        document_number=gr.po_number,
        assigned_role="requester",
        assigned_user_id=requester_id,
        title=f"Create Payment Application: {gr.number} — {gr.title}",
        description=f"GR {gr.number} has been completed. Please create a Payment Application to proceed with vendor payment.",
        vendor=gr.vendor_name,
    ))


async def _attach_gr_pdf(
    db: AsyncSession,
    gr: GoodsReceipt,
    company_name: str,
    token: str | None = None,
    cfg: CompanyConfig | None = None,
) -> None:
    """Generate a confirmed-GR PDF and store it via file server (PRD §3.4)."""
    import asyncio
    loop = asyncio.get_running_loop()
    pdf_bytes = await loop.run_in_executor(
        None, generate_gr_pdf, gr, company_name,
        cfg.pdf_templates if cfg else None,
        cfg.logo_data_url if cfg else None,
    )
    filename = f"{gr.number}.pdf"
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
