"""Report export endpoints — all return CSV downloads."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.core.deps import CurrentUserPayload, SessionDep
from app.crud import reports as rep

router = APIRouter(prefix="/reports", tags=["reports"])


def _csv_response(content: str, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── PR ────────────────────────────────────────────────────────────────────────

@router.get("/pr")
async def export_pr(
    db: SessionDep,
    _: CurrentUserPayload,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    status: str | None = Query(default=None),
    pr_type: int | None = Query(default=None),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    """Export Purchase Requisitions as CSV."""
    csv = await rep.pr_report(
        db,
        date_from=date_from, date_to=date_to,
        status=status, pr_type=pr_type,
        cost_center_id=str(cost_center_id) if cost_center_id else None,
    )
    return _csv_response(csv, f"pr-report-{date.today()}.csv")


# ── PO ────────────────────────────────────────────────────────────────────────

@router.get("/po")
async def export_po(
    db: SessionDep,
    _: CurrentUserPayload,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    status: str | None = Query(default=None),
    po_type: int | None = Query(default=None),
    vendor_id: uuid.UUID | None = Query(default=None),
):
    """Export Purchase Orders as CSV."""
    csv = await rep.po_report(
        db,
        date_from=date_from, date_to=date_to,
        status=status, po_type=po_type,
        vendor_id=str(vendor_id) if vendor_id else None,
    )
    return _csv_response(csv, f"po-report-{date.today()}.csv")


# ── GR ────────────────────────────────────────────────────────────────────────

@router.get("/gr")
async def export_gr(
    db: SessionDep,
    _: CurrentUserPayload,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    status: str | None = Query(default=None),
    gr_type: str | None = Query(default=None),
):
    """Export Goods Receipts as CSV."""
    csv = await rep.gr_report(
        db,
        date_from=date_from, date_to=date_to,
        status=status, gr_type=gr_type,
    )
    return _csv_response(csv, f"gr-report-{date.today()}.csv")


# ── Invoice ───────────────────────────────────────────────────────────────────

@router.get("/invoices")
async def export_invoices(
    db: SessionDep,
    _: CurrentUserPayload,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    status: str | None = Query(default=None),
    vendor_id: uuid.UUID | None = Query(default=None),
):
    """Export Invoices as CSV."""
    csv = await rep.invoice_report(
        db,
        date_from=date_from, date_to=date_to,
        status=status,
        vendor_id=str(vendor_id) if vendor_id else None,
    )
    return _csv_response(csv, f"invoice-report-{date.today()}.csv")


# ── PA ────────────────────────────────────────────────────────────────────────

@router.get("/pa")
async def export_pa(
    db: SessionDep,
    _: CurrentUserPayload,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    status: str | None = Query(default=None),
    pa_type: str | None = Query(default=None),
    vendor_id: uuid.UUID | None = Query(default=None),
):
    """Export Payment Applications as CSV."""
    csv = await rep.pa_report(
        db,
        date_from=date_from, date_to=date_to,
        status=status, pa_type=pa_type,
        vendor_id=str(vendor_id) if vendor_id else None,
    )
    return _csv_response(csv, f"pa-report-{date.today()}.csv")


# ── Budget ────────────────────────────────────────────────────────────────────

@router.get("/budget")
async def export_budget(db: SessionDep, _: CurrentUserPayload):
    """Export Budget accounts (all active L1/L2) as CSV."""
    csv = await rep.budget_report(db)
    return _csv_response(csv, f"budget-report-{date.today()}.csv")


# ── Vendor ────────────────────────────────────────────────────────────────────

@router.get("/vendors")
async def export_vendors(
    db: SessionDep,
    _: CurrentUserPayload,
    category: str | None = Query(default=None),
    active_only: bool = Query(default=False),
):
    """Export Vendors as CSV."""
    csv = await rep.vendor_report(db, category=category, active_only=active_only)
    return _csv_response(csv, f"vendor-report-{date.today()}.csv")
