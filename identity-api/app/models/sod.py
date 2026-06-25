"""Segregation-of-Duties rules — FIN-AUD-003 (Phase 0-B4). identity-api OWNS this table.

Rules are CONFIG, enforced at chokepoints (Phase 0: the unified payment
executor in finance-api reads this table for `self_payment`). Disabling a
rule is a deliberate, auditable act — never delete rows.
"""
import uuid

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class SodRule(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "sod_rules"

    rule_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
