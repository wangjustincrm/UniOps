"""ORM model for Invoice ↔ PO line allocation (multi-PO line-level split)."""
import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class InvoicePoAllocation(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "invoice_po_allocations"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    invoice_line_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="RESTRICT"),
        nullable=False, index=True
    )
    po_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("po_line_items.id", ondelete="RESTRICT"),
        nullable=True, index=True
    )

    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)   # pre-tax
    allocated_tax: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    allocated_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    variance: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
