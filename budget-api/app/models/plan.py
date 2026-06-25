"""Per-CC × year budget plan with monthly lines and optional factor breakdowns.

Revision model (Plan A):
- Each (cost_center_id, fiscal_year) can have multiple versions (v1, v2, …).
- A revision creates a new draft pointing to its parent via `parent_plan_id`.
- Only the version with `is_current = True` is read by balance / actuals queries.
- On approval of a revision, its parent's `is_current` flips to False
  (status stays 'approved' for audit) and the new version takes over.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


PLAN_STATUSES = {"draft", "submitted", "in_review", "approved", "returned", "rejected", "cancelled"}


class BudgetPlan(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "budget_plans"
    __table_args__ = (
        UniqueConstraint(
            "cost_center_id", "fiscal_year", "version",
            name="uq_budget_plan_cc_year_version",
        ),
        # At most one current version per (cc, fy). Enforced via partial unique index
        # so historical (is_current=False) rows don't conflict.
        Index(
            "uq_budget_plan_current",
            "cost_center_id", "fiscal_year",
            unique=True,
            postgresql_where=(lambda: BudgetPlan.is_current.is_(True)),
        ),
        CheckConstraint(
            "status IN ('draft','submitted','in_review','approved','returned','rejected','cancelled')",
            name="ck_plan_status",
        ),
        CheckConstraint("version >= 1", name="ck_plan_version_positive"),
    )

    # FK to epms.cost_centers — logical only (cross-service); no DB constraint.
    cost_center_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # ── Revision lineage (Plan A) ────────────────────────────────────────────
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_plans.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list["BudgetPlanLine"]] = relationship(
        "BudgetPlanLine", back_populates="plan", cascade="all, delete-orphan", lazy="selectin",
        order_by="BudgetPlanLine.month",
    )


class BudgetPlanLine(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "budget_plan_lines"
    __table_args__ = (
        UniqueConstraint("plan_id", "account_id", "month", name="uq_plan_line"),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_plan_line_month"),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_plans.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_accounts.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    plan: Mapped["BudgetPlan"] = relationship("BudgetPlan", back_populates="lines")
    breakdowns: Mapped[list["BudgetPlanBreakdown"]] = relationship(
        "BudgetPlanBreakdown", back_populates="plan_line", cascade="all, delete-orphan", lazy="selectin",
    )


class BudgetPlanBreakdown(UUIDPrimaryKey, Base):
    """Factor combination row under a plan_line — only when account.decomposition_enabled.

    factor_combo example: {"brand": "BRAND_A", "channel": "ONLINE"}
    plan_line.amount = SUM of breakdowns.amount (enforced in CRUD).
    """
    __tablename__ = "budget_plan_breakdowns"

    plan_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_plan_lines.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    factor_combo: Mapped[dict] = mapped_column(JSONB, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    plan_line: Mapped["BudgetPlanLine"] = relationship("BudgetPlanLine", back_populates="breakdowns")
