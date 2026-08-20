"""Read-write mirror of the shared payment_applications table.

expense-api owns PA for Phase 2 OA module. This model maps to the same table
as epms-api's PaymentApplication — both services share the same PostgreSQL DB.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PaymentApplication(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "payment_applications"

    pa_number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    po_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # EPMS Purchase Agreement link (epms-api migration ag02_agreement_links).
    # Mirrored here purely so is_direct() below can tell an agreement-backed
    # EPMS PA apart from OA's own Direct PA — expense-api never writes it.
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)

    invoice_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    gr_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # 'regular' | 'prepayment' — original EPMS field
    pa_type: Mapped[str] = mapped_column(String(20), nullable=False, default="regular")

    subtotal: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    shipping_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    other_charges: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    other_charges_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    prepayment_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    budget_account_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Cost-center binding for the PA (required for PA-DIR after budget-api
    # migration since the L1/L2 catalog is shared across cost centers).
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    @property
    def is_direct(self) -> bool:
        """True only for OA's Direct PA — BOTH po_id AND agreement_id NULL.

        payment_applications has three owners sharing one table: PO-based EPMS
        PAs (po_id set), agreement-based EPMS PAs (agreement_id set, po_id
        NULL — the Purchase Agreement branch), and OA's Direct PAs (both NULL).
        Before agreements existed "po_id is None" was a sound test for a Direct
        PA; it no longer is, and getting it wrong routes an EPMS agreement PA
        through OA's `pa_dir` workflow instead of `pa`.

        Mirrors finance-api/app/models/pa.py::is_direct deliberately. Single
        source of truth for every doc_kind derivation in this service — do not
        re-derive po_id-is-None inline at a new call site.
        """
        return self.po_id is None and self.agreement_id is None
