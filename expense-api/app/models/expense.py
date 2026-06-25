"""ORM models for expense claims (EXP, MIL, TRV, CFM)."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class ExpenseClaim(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "expense_claims"

    claim_number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # EXP|MIL|TRV|CFM

    # Employee
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    employee_name: Mapped[str] = mapped_column(String(255), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # Header
    submission_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # MIL-specific
    vehicle_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vehicle_owned_by: Mapped[str | None] = mapped_column(String(20), nullable=True)  # self|company
    total_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    # TRV-specific
    travel_from_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    travel_to_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    travel_destination: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Financials (computed at submit)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))

    # Workflow
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_over_budget: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Timestamps
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # Relationships
    line_items: Mapped[list["ExpenseLineItem"]] = relationship(
        "ExpenseLineItem", back_populates="claim", cascade="all, delete-orphan",
        order_by="ExpenseLineItem.line_number"
    )
    trip_items: Mapped[list["ExpenseTripItem"]] = relationship(
        "ExpenseTripItem", back_populates="claim", cascade="all, delete-orphan",
        order_by="ExpenseTripItem.trip_number"
    )
    attachments: Mapped[list["ExpenseAttachment"]] = relationship(
        "ExpenseAttachment", back_populates="claim", cascade="all, delete-orphan"
    )
    approval_events: Mapped[list["ExpenseApprovalEvent"]] = relationship(
        "ExpenseApprovalEvent", back_populates="claim", cascade="all, delete-orphan",
        order_by="ExpenseApprovalEvent.created_at"
    )


class ExpenseLineItem(UUIDPrimaryKey, Base):
    """Line items for EXP (and TRV in S4)."""
    __tablename__ = "expense_line_items"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_claims.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)

    budget_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    budget_account_code: Mapped[str] = mapped_column(String(50), nullable=False)
    budget_account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    cost_center_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)   # A
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))  # B
    net_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)     # C = A - B
    # mdm tax_codes code string (Phase 0-B2 ITC groundwork); no cross-service FK
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    claim: Mapped["ExpenseClaim"] = relationship("ExpenseClaim", back_populates="line_items")


class ExpenseTripItem(UUIDPrimaryKey, Base):
    """Trip log rows for MIL mileage claims."""
    __tablename__ = "expense_trip_items"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_claims.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    trip_number: Mapped[int] = mapped_column(Integer, nullable=False)

    trip_date: Mapped[date] = mapped_column(Date, nullable=False)
    from_location: Mapped[str] = mapped_column(String(255), nullable=False)
    to_location: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    is_round_trip: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    distance_km: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    rate_per_km: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    budget_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    budget_account_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    budget_account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    claim: Mapped["ExpenseClaim"] = relationship("ExpenseClaim", back_populates="trip_items")


class ExpenseAttachment(UUIDPrimaryKey, Base):
    __tablename__ = "expense_attachments"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_claims.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    claim: Mapped["ExpenseClaim"] = relationship("ExpenseClaim", back_populates="attachments")


class ExpenseApprovalEvent(UUIDPrimaryKey, Base):
    __tablename__ = "expense_approval_events"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_claims.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # submit|approve|reject|return|pay
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_status: Mapped[str] = mapped_column(String(20), nullable=False)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    claim: Mapped["ExpenseClaim"] = relationship("ExpenseClaim", back_populates="approval_events")
