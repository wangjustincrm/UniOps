"""Association between a Payment Application and the purchase orders it pays.

A PA settling several POs of the same vendor in one payment is the normal AP
case (one cheque / one EFT, one approval, one remittance advice). The header
still carries `payment_applications.po_id` / `po_number`, which stay meaningful
as the PRIMARY PO — the first one selected — so every downstream reader that
only ever knew about one PO (finance mirrors, NC/QBO exports, PDFs of PAs
raised before this table existed) keeps working. This table is the complete
set, and the primary PO is always in it too: anything that asks "which POs does
this PA pay" or "which PAs pay this PO" must read here, never `po_id` alone.

Agreement PAs and OA Direct PAs have no rows here at all (they have no PO).
"""
import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class PaPoLink(UUIDPrimaryKey, Base):
    __tablename__ = "pa_po_links"
    __table_args__ = (
        UniqueConstraint("pa_id", "po_id", name="uq_pa_po_links_pa_po"),
    )

    pa_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_applications.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # RESTRICT mirrors payment_applications.po_id: a PO that has been paid must
    # not vanish under the payment. Data Maintenance's PO cascade removes the
    # PAs first and these rows go with them.
    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    # Denormalized like every other po_number snapshot in this schema, and
    # renumbering cascades to it (admin/po_number.py).
    po_number: Mapped[str] = mapped_column(String(40), nullable=False)
    # 0 = the primary PO (the one mirrored onto the PA header).
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
