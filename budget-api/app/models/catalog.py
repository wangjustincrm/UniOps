"""Budget catalog ORM models: BudgetL1 (category) and BudgetAccount (L2 template).

Both are shared across all CostCenters — they describe WHAT the company tracks
budget for, not how much each CC has. Per-CC budget amounts live in BudgetPlan.
"""
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class BudgetL1(UUIDPrimaryKey, TimestampMixin, Base):
    """L1 budget category — globally unique, shared across all cost centers."""
    __tablename__ = "budget_l1"

    code: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    accounts: Mapped[list["BudgetAccount"]] = relationship(
        "BudgetAccount", back_populates="l1", lazy="selectin",
        order_by="BudgetAccount.sort_order, BudgetAccount.code",
    )


class BudgetAccount(UUIDPrimaryKey, TimestampMixin, Base):
    """L2 budget account — shared template across all cost centers."""
    __tablename__ = "budget_accounts"
    __table_args__ = (
        UniqueConstraint("code", "l1_id", name="uq_budget_account_code_l1"),
    )

    code: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    l1_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_l1.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    # When True, this account's planned amounts are filled in via factor breakdowns,
    # not directly into plan_lines. See PRD §4.2 / DESIGN §4.3.
    decomposition_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    l1: Mapped["BudgetL1"] = relationship("BudgetL1", back_populates="accounts")
    factors: Mapped[list["BudgetAccountFactor"]] = relationship(  # noqa: F821
        "BudgetAccountFactor", back_populates="account", lazy="selectin",
        order_by="BudgetAccountFactor.sort_order",
    )
