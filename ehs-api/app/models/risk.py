"""HSE's risk matrix and the assessments made with it."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class RiskLikelihood(Base):
    __tablename__ = "ehs_risk_likelihood"

    code: Mapped[str] = mapped_column(String(4), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    qualitative: Mapped[str | None] = mapped_column(String(200), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # severity level -> rating. Held whole because the grid is read whole.
    ratings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class RiskSeverity(Base):
    """One band, described across five consequence types.

    HSE's matrix judges severity on whichever consequence applies, so a band is
    five parallel descriptions rather than one.
    """

    __tablename__ = "ehs_risk_severity"

    level: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    people: Mapped[str | None] = mapped_column(String(300), nullable=True)
    downtime: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reputation: Mapped[str | None] = mapped_column(String(300), nullable=True)
    environmental: Mapped[str | None] = mapped_column(String(300), nullable=True)
    asset_damage: Mapped[str | None] = mapped_column(String(300), nullable=True)


class RiskAssessment(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_risk_assessments"

    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    hazard_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    hazard_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Without this, "severity 3" means five different things.
    consequence_type: Mapped[str] = mapped_column(String(20), nullable=False, default="people")
    likelihood_code: Mapped[str] = mapped_column(String(4), nullable=False)
    severity_level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rating: Mapped[str] = mapped_column(String(20), nullable=False)
    likelihood_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    severity_label: Mapped[str | None] = mapped_column(String(300), nullable=True)

    residual_likelihood_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    residual_severity_level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    residual_rating: Mapped[str | None] = mapped_column(String(20), nullable=True)
    controls: Mapped[str | None] = mapped_column(Text, nullable=True)

    assessed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assessed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")
