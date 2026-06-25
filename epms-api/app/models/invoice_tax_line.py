"""Line-level tax split on vendor invoices (Phase 0-B2, FIN-TAX / FIN-EXP-008).

The invoice header keeps tax_amount/total_amount for back-compat; when tax
lines exist they are the source of truth and the header is derived (Σ lines).
tax_code references mdm-api's tax_codes by code string — no cross-service FK
by design. `recoverable` marks ITC-eligible tax (GST/HST/QST) vs not (PST).
"""
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class InvoiceTaxLine(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "invoice_tax_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_code: Mapped[str] = mapped_column(String(20), nullable=False)
    taxable_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    recoverable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
