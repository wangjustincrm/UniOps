"""The Food Safety Risk Assessment on a food-safety near miss (UAT 13).

Kirk: "build a separate risk register on all food safety near misses … Severity
(S) x Probability (P) x Detectability (D) = Risk Priority Number (RPN)" with a
Critical Control Point tick that sets the RPN to 46. Its own table, beside the
incident risk matrix rather than inside it: the scales, the arithmetic and the
bands are HSE's food-safety ones, and the two are read side by side.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class FoodSafetyAssessment(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_food_safety_assessments"
    __table_args__ = (
        CheckConstraint("probability between 1 and 5", name="ck_fsa_probability"),
        CheckConstraint("severity between 1 and 5", name="ck_fsa_severity"),
        CheckConstraint("detectability between 1 and 5", name="ck_fsa_detectability"),
    )

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_incidents.id", ondelete="CASCADE"),
        nullable=False, index=True)
    ccp_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    probability: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    severity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    detectability: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Stored as worked out when it was assessed, so a later change to the
    # bands does not rewrite what was decided at the time.
    rpn: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    band: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assessed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
