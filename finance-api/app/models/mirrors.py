"""Read-only / status-write mirrors of other services' tables.

finance-api needs these for the unified payment executor (Phase 0-B1.5):
- Invoice (epms-api owns): mark linked invoices paid on PA-PO payment
- CompanyConfig (epms-api owns): role_management for can_pay assignments
- ExpenseClaim (expense-api owns): status flip + posting amounts on claim payment
Schemas are owned by their home services — never migrate these from finance-api.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class SodRule(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror of identity-api's sod_rules — the payment executor
    enforces `self_payment` here (FIN-AUD-003)."""
    __tablename__ = "sod_rules"

    rule_code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CostCenter(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror of the shared `cost_centers` master (epms/mdm own it).
    Finance resolves posting/JV `cost_center_id` -> code/name for the account
    balance report + Budget Actual (code prefix MOH/RD/SELL/GA -> category).
    NC cost centers map 1:1 to these (via Budget Config)."""
    __tablename__ = "cost_centers"

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class Department(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror of the shared `departments` master (epms owns schema).
    Columns verified against information_schema 2026-07-13:
    id/code/name/is_active/created_at/updated_at (all NOT NULL)."""
    __tablename__ = "departments"

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BudgetAccount(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror of budget_accounts (budget-api owns schema). Column
    SUBSET verified against information_schema 2026-07-13 — resolves the
    income_expense_item dimension (CRM code) to a display name."""
    __tablename__ = "budget_accounts"

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ErpSupplier(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror of mdm-api's erp_suppliers (integration-API synced).
    Column SUBSET verified against information_schema 2026-07-14 — resolves the
    supplier partner dimension to code/name."""
    __tablename__ = "erp_suppliers"

    erp_supplier_code: Mapped[str] = mapped_column(String(50), nullable=False)
    supplier_name: Mapped[str] = mapped_column(String(255), nullable=False)


class Invoice(UUIDPrimaryKey, TimestampMixin, Base):
    """epms-api owns schema. A2 extends the mirror for AP accrual + open items
    (columns verified against information_schema 2026-06-12)."""
    __tablename__ = "invoices"

    internal_ref: Mapped[str] = mapped_column(String(30), nullable=False)
    vendor_invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)        # pre-tax
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # PO link (physical column exists; mirror reads it for ap_invoices backfill)
    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)


class InvoiceTaxLine(UUIDPrimaryKey, TimestampMixin, Base):
    """epms-api owns schema (B2's line-level tax split) — read for accrual ITC."""
    __tablename__ = "invoice_tax_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_code: Mapped[str] = mapped_column(String(20), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    recoverable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# NOTE: no TimestampMixin — the physical company_config table has no
# created_at column (latent 500 found via identity B4; approval-api's mirror
# got this right all along).
class CompanyConfig(UUIDPrimaryKey, Base):
    __tablename__ = "company_config"

    role_management: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Task(UUIDPrimaryKey, TimestampMixin, Base):
    """Shared workflow inbox (epms-api owns the schema). The payment executor
    closes the open process_pa / process_expense task when money goes out —
    the approval engine used to do this in its (now removed) process branch."""
    __tablename__ = "tasks"

    document_type: Mapped[str] = mapped_column(String(20), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class User(UUIDPrimaryKey, Base):
    """Read-only mirror (epms owns schema) — actor_name lookup for audit rows."""
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)


class ExpenseApprovalEvent(UUIDPrimaryKey, Base):
    """OA claim audit trail (expense-api owns schema). Written by the payment
    executor on claim payment (A0 — moved from expense-api's after_payment).
    NOTE: physical table has created_at only, NO updated_at (verified via
    information_schema — do not add TimestampMixin)."""
    __tablename__ = "expense_approval_events"

    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    from_status: Mapped[str] = mapped_column(String(20), nullable=False)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class ExpenseLineItem(UUIDPrimaryKey, Base):
    """Read-only subset for budget booking aggregation (expense-api owns schema)."""
    __tablename__ = "expense_line_items"

    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    budget_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)  # B2 line tax code (A5 ITC)


class ExpenseTripItem(UUIDPrimaryKey, Base):
    """Read-only subset — MIL trips. NOTE: physical table has NO cost_center_id."""
    __tablename__ = "expense_trip_items"

    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    budget_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)


class ExpenseClaim(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "expense_claims"

    claim_number: Mapped[str] = mapped_column(String(30), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # SoD: claimant
    employee_name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))


class ExpenseInvoice(UUIDPrimaryKey, Base):
    """Read-only mirror subset (expense-api owns schema) — OA invoice -> Direct PA
    link for the NC AP export dimension chain. Verified 2026-07-14."""
    __tablename__ = "expense_invoices"

    pa_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class InvoicePoAllocation(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror subset (epms-api owns schema) — line-level multi-PO
    allocation amounts for the NC AP export. Verified 2026-07-14."""
    __tablename__ = "invoice_po_allocations"

    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    po_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    allocated_tax: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)


class PurchaseRequest(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror subset (epms-api owns schema) — PR head carries the
    budget dims (CC 100% coverage on dev). Verified 2026-07-14."""
    __tablename__ = "purchase_requests"

    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    budget_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
