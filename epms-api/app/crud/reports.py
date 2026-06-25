"""Report generation: query + CSV serialisation."""
from __future__ import annotations

import csv
import io
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# NOTE: Budget data moved to budget-api (:8007). Budget reports now return an
# empty CSV; the front-end should download budget reports from budget-api
# directly (GET /api/v1/catalog/export, or a dedicated /reports endpoint added there).
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor


def _csv(rows: list[list], headers: list[str]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(rows)
    return buf.getvalue()


def _dt(v) -> str:
    return v.strftime("%Y-%m-%d %H:%M") if v else ""


def _d(v) -> str:
    return str(v) if v else ""


# ── PR ────────────────────────────────────────────────────────────────────────

PR_HEADERS = [
    "number", "title", "type", "status", "currency", "amount",
    "vendor_name", "budget_code", "cost_center_id",
    "required_by", "submitted_at", "created_at",
]


async def pr_report(
    db: AsyncSession,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    pr_type: int | None = None,
    cost_center_id: str | None = None,
) -> str:
    q = select(PurchaseRequest)
    if date_from:
        q = q.where(PurchaseRequest.created_at >= date_from)
    if date_to:
        q = q.where(PurchaseRequest.created_at <= date_to)
    if status:
        q = q.where(PurchaseRequest.status == status)
    if pr_type:
        q = q.where(PurchaseRequest.type == pr_type)
    if cost_center_id:
        q = q.where(PurchaseRequest.cost_center_id == cost_center_id)
    q = q.order_by(PurchaseRequest.created_at.desc())

    result = await db.execute(q)
    rows = [
        [
            r.number, r.title, r.type, r.status, r.currency,
            r.amount, r.vendor_name or "", r.budget_code or "",
            str(r.cost_center_id) if r.cost_center_id else "",
            _d(r.required_by), _dt(r.submitted_at), _dt(r.created_at),
        ]
        for r in result.scalars()
    ]
    return _csv(rows, PR_HEADERS)


# ── PO ────────────────────────────────────────────────────────────────────────

PO_HEADERS = [
    "number", "title", "type", "status", "currency",
    "subtotal", "tax_amount", "total",
    "vendor_name", "budget_code", "pr_number",
    "expected_delivery", "updated_at", "created_at",
]


async def po_report(
    db: AsyncSession,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    po_type: int | None = None,
    vendor_id: str | None = None,
) -> str:
    q = select(PurchaseOrder)
    if date_from:
        q = q.where(PurchaseOrder.created_at >= date_from)
    if date_to:
        q = q.where(PurchaseOrder.created_at <= date_to)
    if status:
        q = q.where(PurchaseOrder.status == status)
    if po_type:
        q = q.where(PurchaseOrder.type == po_type)
    if vendor_id:
        q = q.where(PurchaseOrder.vendor_id == vendor_id)
    q = q.order_by(PurchaseOrder.created_at.desc())

    result = await db.execute(q)
    rows = [
        [
            r.number, r.title, r.type, r.status, r.currency,
            r.subtotal, r.tax_amount, r.total,
            r.vendor_name, r.budget_code or "", r.pr_number or "",
            _d(r.expected_delivery), _dt(r.updated_at), _dt(r.created_at),
        ]
        for r in result.scalars()
    ]
    return _csv(rows, PO_HEADERS)


# ── GR ────────────────────────────────────────────────────────────────────────

GR_HEADERS = [
    "number", "title", "gr_type", "status", "currency",
    "vendor_name", "po_number", "storage_location",
    "acknowledged_by", "acknowledged_at",
    "collected_by", "collected_at", "created_at",
]


async def gr_report(
    db: AsyncSession,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    gr_type: str | None = None,
) -> str:
    q = select(GoodsReceipt)
    if date_from:
        q = q.where(GoodsReceipt.created_at >= date_from)
    if date_to:
        q = q.where(GoodsReceipt.created_at <= date_to)
    if status:
        q = q.where(GoodsReceipt.status == status)
    if gr_type:
        q = q.where(GoodsReceipt.gr_type == gr_type)
    q = q.order_by(GoodsReceipt.created_at.desc())

    result = await db.execute(q)
    rows = [
        [
            r.number, r.title, r.gr_type, r.status, r.currency,
            r.vendor_name, r.po_number, r.storage_location or "",
            r.acknowledged_by or "", _dt(r.acknowledged_at),
            r.collected_by or "", _dt(r.collected_at), _dt(r.created_at),
        ]
        for r in result.scalars()
    ]
    return _csv(rows, GR_HEADERS)


# ── Invoice ───────────────────────────────────────────────────────────────────

INVOICE_HEADERS = [
    "internal_ref", "vendor_invoice_number", "vendor_name",
    "currency", "amount", "tax_amount", "total_amount",
    "status", "invoice_date", "due_date",
    "po_number", "gr_number", "variance", "variance_pct",
    "exception_resolution", "created_at",
]


async def invoice_report(
    db: AsyncSession,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    vendor_id: str | None = None,
) -> str:
    q = select(Invoice)
    if date_from:
        q = q.where(Invoice.created_at >= date_from)
    if date_to:
        q = q.where(Invoice.created_at <= date_to)
    if status:
        q = q.where(Invoice.status == status)
    if vendor_id:
        q = q.where(Invoice.vendor_id == vendor_id)
    q = q.order_by(Invoice.created_at.desc())

    result = await db.execute(q)
    rows = [
        [
            r.internal_ref, r.vendor_invoice_number, r.vendor_name,
            r.currency, r.amount, r.tax_amount, r.total_amount,
            r.status, _d(r.invoice_date), _d(r.due_date),
            r.po_number or "", r.gr_number or "",
            r.variance or "", r.variance_pct or "",
            r.exception_resolution or "", _dt(r.created_at),
        ]
        for r in result.scalars()
    ]
    return _csv(rows, INVOICE_HEADERS)


# ── PA ────────────────────────────────────────────────────────────────────────

PA_HEADERS = [
    "pa_number", "title", "pa_type", "status", "currency",
    "subtotal", "tax_amount", "payment_amount",
    "vendor_name", "po_number",
    "prepayment_pct", "settlement_status",
    "submitted_at", "created_at",
]


async def pa_report(
    db: AsyncSession,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    pa_type: str | None = None,
    vendor_id: str | None = None,
) -> str:
    q = select(PaymentApplication)
    if date_from:
        q = q.where(PaymentApplication.created_at >= date_from)
    if date_to:
        q = q.where(PaymentApplication.created_at <= date_to)
    if status:
        q = q.where(PaymentApplication.status == status)
    if pa_type:
        q = q.where(PaymentApplication.pa_type == pa_type)
    if vendor_id:
        q = q.where(PaymentApplication.vendor_id == vendor_id)
    q = q.order_by(PaymentApplication.created_at.desc())

    result = await db.execute(q)
    rows = [
        [
            r.pa_number, r.title, r.pa_type, r.status, r.currency,
            r.subtotal, r.tax_amount, r.payment_amount,
            r.vendor_name, r.po_number,
            r.prepayment_pct or "", r.settlement_status or "",
            _dt(r.submitted_at), _dt(r.created_at),
        ]
        for r in result.scalars()
    ]
    return _csv(rows, PA_HEADERS)


# ── Budget ────────────────────────────────────────────────────────────────────

BUDGET_HEADERS = [
    "l1_code", "l1_name", "account_code", "account_name",
    "annual_budget", "committed", "actual_spent", "available", "utilisation_pct",
]


async def budget_report(db: AsyncSession) -> str:  # noqa: ARG001
    # Budget data now lives in budget-api. The front-end should call
    # budget-api GET /api/v1/catalog/export instead.
    return _csv([], BUDGET_HEADERS)


# ── Vendor ────────────────────────────────────────────────────────────────────

VENDOR_HEADERS = [
    "code", "name", "category", "contact_name", "contact_email",
    "phone", "payment_terms", "currency", "is_active", "created_at",
]


async def vendor_report(
    db: AsyncSession,
    *,
    category: str | None = None,
    active_only: bool = False,
) -> str:
    q = select(Vendor)
    if category:
        q = q.where(Vendor.category == category)
    if active_only:
        q = q.where(Vendor.is_active.is_(True))
    q = q.order_by(Vendor.code)

    result = await db.execute(q)
    rows = [
        [
            r.code, r.name, r.category, r.contact_name, r.contact_email,
            r.phone or "", r.payment_terms, r.currency,
            r.is_active, _dt(r.created_at),
        ]
        for r in result.scalars()
    ]
    return _csv(rows, VENDOR_HEADERS)
