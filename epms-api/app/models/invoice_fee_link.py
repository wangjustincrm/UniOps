"""ORM model for a fee invoice's link to a raw-material PO (PO Fee Invoices).

Design: docs/superpowers/specs/2026-10-23-po-fee-invoices-design.md.

A freight or customs-clearance invoice comes from a forwarder / broker, not
from the PO's own vendor, and is paid by a Direct PA. This row records which
NC raw-material PO — and which of its arrivals — the charge belongs to, so the
PO can show whether its fee invoices have all come in.

Deliberately NOT invoices.po_id: ck_invoices_direct_no_po forbids a PO on a
Direct-route invoice (a PO PA and a Direct PA could otherwise both pay it).
This link is orthogonal to matching and payment — nothing that decides what
gets paid, or to whom, reads it.
"""
import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

FEE_TYPES = ("freight", "customs", "other")


class InvoiceFeeLink(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "invoice_fee_links"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # invoices.line_items[].id — a customs invoice carries one line per PO.
    # NULL = the whole invoice (a freight bill covering several arrivals).
    invoice_line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    fee_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # CASCADE: Data Maintenance deleting a PO takes its links with it. The NC
    # full reload never deletes a PO that has one (service._full_reload_delete).
    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # SET NULL: a deleted receipt turns the link into "arrival not chosen",
    # which the PO panel shows — the invoice does not silently drop off the PO.
    gr_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goods_receipts.id", ondelete="SET NULL"),
        nullable=True, index=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
