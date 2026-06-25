"""Expense policy configuration — singleton row."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExpensePolicyConfig(Base):
    __tablename__ = "expense_policy_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Tax
    hst_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=Decimal("0.13"))

    # Mileage
    mileage_rate_per_km: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=Decimal("0.72")
    )
    mileage_budget_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    max_km_per_claim: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)

    # Travel meal limits (CAD) — for TRV in S4
    meal_breakfast_limit: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, default=Decimal("23.00")
    )
    meal_lunch_limit: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, default=Decimal("23.00")
    )
    meal_dinner_limit: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, default=Decimal("46.00")
    )
    meal_incidental_limit: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, default=Decimal("17.30")
    )

    # CFM custom form definitions — list of {code, name, fields, is_active}
    from sqlalchemy.dialects.postgresql import JSONB
    custom_forms: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
