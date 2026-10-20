"""Emailing an address ahead of certificates expiring.

Round 8 (open box): "Create a new rule for managing training certificates that
are about to expire. This rule will allow notifications to be emailed to a
specified email address and have a field for the number of days notice."

A rule is an address and a number of days. The notice log is what makes the
sweep send each certificate to each rule once: it is keyed on the expiry date
as well, so a certificate renewed to a new date is told about again.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class CertExpiryRule(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_cert_expiry_rules"

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    days_before: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class CertExpiryNotice(UUIDPrimaryKey, Base):
    __tablename__ = "ehs_cert_expiry_notices"
    __table_args__ = (UniqueConstraint(
        "rule_id", "source_type", "source_id", "expires_on",
        name="uq_ehs_cert_expiry_notice"),)

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_cert_expiry_rules.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # 'training' (a training record) or 'cert' (a worker certification).
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    expires_on: Mapped[date] = mapped_column(Date, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
