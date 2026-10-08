"""Read-only mirror of EPMS `pa_po_links` — the POs a payment application pays.

EPMS lets one PA settle several purchase orders in a single payment, so
`payment_applications.po_id` is only the PRIMARY one. Finance reads this table
wherever "the POs behind this payment" is the real question — today that is the
unclaimed-invoice guard in crud/payment_execute.py, which would otherwise let a
payment go out while a second PO's invoice stayed open in AP.

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
