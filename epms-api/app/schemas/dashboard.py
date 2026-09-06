"""Pydantic schemas for Dashboard aggregation API."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


# ── Shared building blocks ───────────────────────────────────────────────────

class KpiCard(BaseModel):
    title: str
    value: str          # pre-formatted string, e.g. "42" or "CAD 1,234.56"
    subtitle: str | None = None
    alert: bool = False


class ApprovalItem(BaseModel):
    id: uuid.UUID
    doc_type: str           # PR | PO | PA
    number: str
    title: str
    amount: Decimal
    currency: str
    status: str
    submitted_days_ago: int
    href: str
    requester_name: str = ""
    dept_name: str = ""


class BudgetGroupRow(BaseModel):
    l1_code: str
    l1_name: str
    annual_budget: Decimal
    committed: Decimal
    actual_spent: Decimal
    available: Decimal
    utilisation_pct: float


class BudgetOverview(BaseModel):
    total_budget: Decimal
    total_committed: Decimal
    total_spent: Decimal
    utilisation_pct: float
    groups: list[BudgetGroupRow]


# ── Document summary rows ───────────────────────────────────────────────────

class PoRow(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    vendor_name: str
    total: Decimal
    currency: str
    status: str
    created_at: datetime


class GrRow(BaseModel):
    id: uuid.UUID
    number: str
    gr_type: str
    po_number: str
    vendor_name: str
    currency: str
    status: str
    created_at: datetime


class InvoiceRow(BaseModel):
    id: uuid.UUID
    internal_ref: str
    vendor_name: str
    po_number: str | None
    total_amount: Decimal
    currency: str
    status: str
    created_at: datetime


class VendorRow(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    category: str
    contact_name: str
    payment_terms: str
    is_active: bool
    created_at: datetime


class PaRow(BaseModel):
    id: uuid.UUID
    pa_number: str
    vendor_name: str
    po_number: str
    # Every PO the payment settles, primary first (a PA may cover several).
    # po_number above stays the primary one; the dashboard row shows it plus a
    # "+N" so a multi-PO payment does not read as a single-PO one.
    po_numbers: list[str] = Field(default_factory=list)
    pa_type: str
    payment_amount: Decimal
    currency: str
    status: str
    created_at: datetime


class PaOverview(BaseModel):
    total_count: int
    pending_count: int
    pending_value: Decimal
    processed_value: Decimal


# ── PR Pipeline ─────────────────────────────────────────────────────────────

class PipelineGr(BaseModel):
    id: uuid.UUID
    number: str
    gr_type: str
    status: str


class PipelineInvoice(BaseModel):
    id: uuid.UUID
    internal_ref: str
    status: str
    total_amount: Decimal


class PipelinePa(BaseModel):
    id: uuid.UUID
    pa_number: str
    status: str
    payment_amount: Decimal


class PipelinePo(BaseModel):
    id: uuid.UUID
    number: str
    status: str
    total: Decimal
    vendor_name: str
    grs: list[PipelineGr]
    invoices: list[PipelineInvoice]
    pas: list[PipelinePa]


class PrPipelineItem(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    status: str
    amount: Decimal
    currency: str
    pos: list[PipelinePo]


# ── Status breakdown (auditor) ───────────────────────────────────────────────

class StatusBreakdown(BaseModel):
    pr: dict[str, int]
    po: dict[str, int]
    gr: dict[str, int]
    pa: dict[str, int]


# ── Users by role (system_admin) ────────────────────────────────────────────

class RoleCount(BaseModel):
    role: str
    count: int


# ── Main response ───────────────────────────────────────────────────────────

class DashboardResponse(BaseModel):
    role: str
    kpis: list[KpiCard]

    # Approver / finance roles
    pending_approvals: list[ApprovalItem] | None = None
    budget_overview: BudgetOverview | None = None
    pa_overview: PaOverview | None = None

    # Document lists
    recent_pos: list[PoRow] | None = None
    recent_grs: list[GrRow] | None = None
    recent_invoices: list[InvoiceRow] | None = None
    recent_vendors: list[VendorRow] | None = None
    pa_in_review: list[PaRow] | None = None

    # Requester
    pr_pipeline: list[PrPipelineItem] | None = None

    # Auditor
    status_breakdown: StatusBreakdown | None = None

    # System admin
    users_by_role: list[RoleCount] | None = None
