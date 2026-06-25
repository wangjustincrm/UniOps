"""Read-only mirrors of EPMS tables needed by expense-api.

These models map to the shared PostgreSQL database without any ownership
or migration responsibility — EPMS manages the schema.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EpmsCostCenter(Base):
    """Read-only mirror of EPMS cost_centers table."""
    __tablename__ = "cost_centers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# NOTE: EpmsBudgetL1 mirror removed — budget catalog is now owned by budget-api (:8007).
# Frontend code that needs a CC→L1→Account hierarchy calls budget-api directly.


class EpmsVendor(Base):
    """Read-only mirror of EPMS vendors table for vendor name lookup."""
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EpmsPurchaseRequest(Base):
    """Read-only mirror of EPMS purchase_requests — used for invoice scope subqueries."""
    __tablename__ = "purchase_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class EpmsPurchaseOrder(Base):
    """Read-only mirror of EPMS purchase_orders — used for invoice scope subqueries."""
    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    pr_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class EpmsInvoice(Base):
    """Read-only mirror of EPMS invoices table."""
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    vendor_invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    internal_ref: Mapped[str] = mapped_column(String(30), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unmatched")
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
