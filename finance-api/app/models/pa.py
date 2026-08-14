"""Read-only mirror of EPMS PaymentApplication — Finance Core reads for AP payables view."""
import uuid
from decimal import Decimal
from sqlalchemy import Boolean, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin
from datetime import datetime


class PaymentApplication(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "payment_applications"

    pa_number: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    pa_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # po_id IS NULL for OA Direct PAs (PA-DIR) — earlier nullable=False was wrong
    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Agreement-sourced EPMS PA (epms-api Task 4/7): po_id NULL but this is set
    # instead. Distinguishing it from an OA Direct PA (BOTH po_id and
    # agreement_id NULL) is required — see doc_kind derivation in
    # crud/payment_execute.py and crud/payment_batch.py: pa_dir means Direct
    # PA specifically, not merely "no PO".
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    invoice_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    payment_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    expected_settlement_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Written by the payment executor when the PA is paid (see crud.payment_execute).
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    budget_account_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    @property
    def is_direct(self) -> bool:
        """True only for OA's Direct PA — BOTH po_id AND agreement_id NULL.

        payment_applications has three owners sharing one table: PO-based EPMS
        PAs (po_id set), agreement-based EPMS PAs (agreement_id set, po_id
        NULL), and OA's Direct PAs (both NULL). doc_kind's 'pa' vs 'pa_dir'
        split is really "does finance-api's AP/GL treatment apply" vs "is this
        OA's own PO-less flow" — an agreement PA is finance-owned EPMS spend,
        not a Direct PA, even though po_id alone can't tell them apart.
        Single source of truth for every doc_kind derivation in this service
        (crud/payment_execute.py, crud/payment_batch.py) — do not re-derive
        po_id-is-None inline at a new call site.
        """
        return self.po_id is None and self.agreement_id is None
