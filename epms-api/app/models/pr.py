"""ORM models for Purchase Request (PR) and its line items."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PurchaseRequest(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "purchase_requests"

    number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # 1=Raw Materials, 2=Consumables, 3=Spare Parts, 4=Service, 5=Fixed Assets, 6=Software
    type: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft", index=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))

    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_partners.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_centers.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    cost_center_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    budget_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Selected decomposition factor combo for the referenced budget account.
    # Required client-side when the Account has decomposition_enabled=True; nullable
    # at the DB level so PRs against non-decomposed accounts (and legacy rows) work.
    # Shape: {factor_code: value_code} e.g. {"brand": "BRAND_A", "channel": "ONLINE"}.
    # Mirrors budget-api `budget_plan_breakdowns.factor_combo` for cross-system joins.
    factor_combo: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    project_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    required_by: Mapped[date | None] = mapped_column(Date, nullable=True)
    # When the service / project is expected to be finished. Collected only for
    # procurement types 4 (Service) and 6 (Project-Related) — the same pair
    # app/api/v1/gr.py routes through the service GR flow — and required at
    # submit time for those two. Nullable at the DB level because every row
    # created before this column existed has no value, and the due-date sweep
    # (app/tasks/service_gr_due.py) deliberately skips those rather than
    # guessing a date for them.
    service_completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    delivery_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_prepaid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Over-budget pre-approval (OBG-001~008): when a PR's projected balance
    # for its budget account is negative, this flag is set on create() and
    # the approval engine injects extra pre-approval steps per
    # CompanyConfig.budget_admin_config.over_budget_mode.
    over_budget: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    over_budget_justification: Mapped[str | None] = mapped_column(Text, nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Linked PO (set when PO is created from this PR)
    po_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    po_number: Mapped[str | None] = mapped_column(String(30), nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Service/project OWNER — the person who confirms the service was delivered
    # and creates the GR. Collected by the Create PR form only for procurement
    # types 4 (Service) and 6 (Project-Related), defaulted there to the
    # requester and adjustable from it, because the person who raises the
    # requisition is not always the person who can say the work finished.
    #
    # NULL is not "unset": it means "the requester", which is what every row
    # written before this column existed means too. Never read it directly —
    # app/crud/pr_owner.py owns that fallback, and every receipt-task routing
    # point goes through it.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )

    line_items: Mapped[list["PrLineItem"]] = relationship(
        "PrLineItem", back_populates="pr", cascade="all, delete-orphan", lazy="selectin",
        order_by="PrLineItem.sort_order"
    )


class PrLineItem(UUIDPrimaryKey, Base):
    __tablename__ = "pr_line_items"

    pr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_requests.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    material_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    supplier_item_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    pr: Mapped["PurchaseRequest"] = relationship("PurchaseRequest", back_populates="line_items")
