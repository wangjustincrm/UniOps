"""Single-row system settings table for budget-api."""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class BudgetSettings(UUIDPrimaryKey, Base):
    __tablename__ = "budget_settings"
    __table_args__ = (
        CheckConstraint("max_factors_per_account BETWEEN 1 AND 50", name="ck_max_factors_range"),
    )

    max_factors_per_account: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
