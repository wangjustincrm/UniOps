"""ORM model for Vendor — the EPMS supplier view of business_partners (B3).

Physical table moved to business_partners (mdm-owned, supplier+customer
roles). This model maps the supplier-relevant column subset; extra partner
columns (tax_number, customer_type, ...) are invisible to this ORM and
maintained via /mdm/v1/partners. Inserting here creates a supplier
(is_supplier defaults true). The old `vendors` name is a compat VIEW.
"""
from decimal import Decimal

from sqlalchemy import Boolean, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Vendor(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "business_partners"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    erp_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_terms: Mapped[str] = mapped_column(String(20), nullable=False, default="net30")
    max_prepayment_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # role flags (B3): EPMS lists must exclude customer-only partners
    is_supplier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_customer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
