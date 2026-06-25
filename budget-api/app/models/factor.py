"""Decomposition factors — both reusable library templates and per-Account copies.

`FactorTemplate` / `FactorTemplateValue` form a reusable library that an admin
maintains in **Portal → Finance → Factor Library**. When a template is attached
to a `BudgetAccount` (decomposition_enabled=True), its factor_code/name and
values are **copied** into `BudgetAccountFactor` / `BudgetAccountFactorValue`.
After the copy, the per-Account rows are independent — editing a template later
does not mutate previously attached accounts. This protects historical plan
breakdowns that store factor_code in `budget_plan_breakdowns.factor_combo`.

Per-Account factors can still be hand-typed from scratch — the library is
optional. The Account-level constraint is still 1..N factors (capped by
`BudgetSettings.max_factors_per_account`).
"""
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


# ── Library templates (Portal → Finance → Factor Library) ─────────────────────

class FactorTemplate(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "factor_templates"

    # Globally unique within the library. Acts as the default factor_code when
    # the template is copied onto an Account (admin can override at copy time).
    factor_code: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    factor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # cascade=all,delete-orphan mirrors BudgetAccountFactor → values so a single
    # DELETE removes the value rows in one go.
    values: Mapped[list["FactorTemplateValue"]] = relationship(
        "FactorTemplateValue", back_populates="template", lazy="selectin",
        cascade="all, delete-orphan",
        order_by="FactorTemplateValue.sort_order",
    )


class FactorTemplateValue(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "factor_template_values"
    __table_args__ = (
        UniqueConstraint("template_id", "value_code", name="uq_factor_template_value_code"),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factor_templates.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    value_code: Mapped[str] = mapped_column(String(50), nullable=False)
    value_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    template: Mapped["FactorTemplate"] = relationship("FactorTemplate", back_populates="values")


class BudgetAccountFactor(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "budget_account_factors"
    __table_args__ = (
        UniqueConstraint("account_id", "factor_code", name="uq_account_factor_code"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_accounts.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    factor_code: Mapped[str] = mapped_column(String(30), nullable=False)
    factor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    account: Mapped["BudgetAccount"] = relationship("BudgetAccount", back_populates="factors")  # noqa: F821
    # cascade=all,delete-orphan: deleting a factor also deletes its values.
    # Without this, SQLAlchemy's default is to UPDATE factor_id=NULL on each
    # child first — which violates the NOT NULL constraint and blocks delete.
    # The DB-level FK is ON DELETE RESTRICT, but that doesn't fire because
    # SQLAlchemy issues a DELETE per child *before* deleting the parent.
    values: Mapped[list["BudgetAccountFactorValue"]] = relationship(
        "BudgetAccountFactorValue", back_populates="factor", lazy="selectin",
        cascade="all, delete-orphan",
        order_by="BudgetAccountFactorValue.sort_order",
    )


class BudgetAccountFactorValue(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "budget_account_factor_values"
    __table_args__ = (
        UniqueConstraint("factor_id", "value_code", name="uq_factor_value_code"),
    )

    factor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budget_account_factors.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    value_code: Mapped[str] = mapped_column(String(50), nullable=False)
    value_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    factor: Mapped["BudgetAccountFactor"] = relationship("BudgetAccountFactor", back_populates="values")
