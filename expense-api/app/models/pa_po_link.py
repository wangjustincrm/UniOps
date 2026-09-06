"""Read-only mirror of EPMS `pa_po_links` — the POs a payment application pays.

EPMS lets one PA settle several purchase orders in a single payment, so
`payment_applications.po_id` is only the PRIMARY one. OA (expense-api) reads this table
wherever "the POs behind this payment" is the real question — today that is the by-PO lookup behind EPMS's document chain tree, which would
otherwise show no payment at all on a PO that a PA covers second.

Mirror model: EPMS owns the table and its migration (epms-api
`pa01_pa_po_links`); nothing here writes to it.
"""
import uuid

from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class PaPoLink(UUIDPrimaryKey, Base):
    __tablename__ = "pa_po_links"

    pa_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    po_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    po_number: Mapped[str] = mapped_column(String(40), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
