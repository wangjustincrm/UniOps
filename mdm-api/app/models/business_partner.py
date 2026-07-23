"""Business Partner master — suppliers AND customers in one table (Phase 0-B3).

Supersedes the EPMS-owned `vendors` table: rows were migrated id-preserving
(mdm migration 0004), EPMS document FKs were repointed (epms migration x5),
and `vendors` lives on as a compatibility VIEW (is_supplier rows only).

FIN-MD-006 fields: tax_number, customer_type + province feed the B2 tax
determination engine; credit_limit gates AR (Phase c). CRM (Phase b) attaches
customers here — one partner, many roles.
"""
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class BusinessPartner(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "business_partners"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    erp_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    # Where remittance advice is sent. Falls back to contact_email when empty
    # (finance-api resolves the fallback, not the DB).
    remittance_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_terms: Mapped[str] = mapped_column(String(20), nullable=False, default="net30")
    max_prepayment_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── roles (B3) ────────────────────────────────────────────────────────────
    is_supplier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_customer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── FIN-MD-006 ────────────────────────────────────────────────────────────
    tax_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    customer_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # business|consumer|export
    province: Mapped[str | None] = mapped_column(String(2), nullable=True)        # place of supply
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
