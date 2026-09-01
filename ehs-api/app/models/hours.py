"""Total hours worked, one figure per month.

The injury rates every safety programme is judged on — TRIR, LTIFR, DART — are
a count of incidents over hours worked. UniOps holds no hours data and Safety
should not be the system that collects it, so this is the smallest thing that
makes the rates computable: HSE enters one number a month from payroll.

Editable after the fact, at HSE's request: payroll corrections happen, and a
figure that could only be entered once would leave the rate wrong forever with
no way to say so.

Stored as a month rather than a date range so that "which month is missing" is
answerable. A rate computed over a period with a gap in it is not a smaller
rate — it is a wrong one, and the reports say so rather than quietly dividing
by whatever was there.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class HoursWorked(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_hours_worked"
    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_ehs_hours_worked_month"),
    )

    year: Mapped[int] = mapped_column(SmallInteger, nullable=False, index=True)
    month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Numeric, not integer: payroll reports part hours, and rounding a plant's
    # monthly total to whole hours moves the third decimal of the rate.
    hours: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    entered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    entered_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
