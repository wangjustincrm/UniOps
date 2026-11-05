"""Which budget alert level each cost center × account was last emailed at.

app/tasks/budget_alerts.py (ISS-0146 ③) emails when a line crosses UP into
the warning or over-budget level, once per crossing: this row is that mark.
It is moved down (or removed) when usage drops back, so a later crossing
alerts again.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class BudgetAlertState(UUIDPrimaryKey, Base):
    __tablename__ = "budget_alert_states"
    __table_args__ = (
        UniqueConstraint("cost_center_id", "account_id", "fiscal_year",
                         name="uq_budget_alert_state_line"),
    )

    cost_center_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    # warning | over
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    used_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    alerted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
