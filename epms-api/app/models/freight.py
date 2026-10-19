"""ORM models for courier freight payment (Freight).

Design: docs/superpowers/specs/2026-10-19-freight-payment-design.md.

Buyers register a courier tracking number against the cost centre that bears
the cost (FreightShipment). The carrier's summary invoice is stored as an
ordinary EPMS Invoice (match_route='freight'); its per-shipment detail goes to
FreightInvoiceLine, each line matched to a shipment or assigned a cost centre
by hand. A Freight PA (payment_applications.source='freight') then pays one or
more matched invoices of one carrier, and PaCostAllocation snapshots the split
by (invoice, cost centre) — that snapshot is what the NC AP export reads, one
body row per cost centre (D12).
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

# The fixed budget account every freight cost lands on (D6). Stored bare —
# never the "code-name" composite some legacy budget_code columns hold.
FREIGHT_BUDGET_CODE = "CRM0150102"


class FreightCarrier(UUIDPrimaryKey, TimestampMixin, Base):
    """A courier we pay through Freight. Doubles as the whitelist: only these
    vendors can be paid by a PO-less Freight PA (design §1.1)."""
    __tablename__ = "freight_carriers"

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=False, unique=True)
    # UPS | FEDEX | PUROLATOR | … — selects the reading hints for the AI
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Optional regex a normalised tracking number must match (e.g. ^1Z[0-9A-Z]{16}$).
    tracking_pattern: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Where charges with no tracking number go by default (pickups, late fees).
    default_cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_centers.id", ondelete="RESTRICT"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                            server_default="true")


class FreightShipment(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "freight_shipments"
    __table_args__ = (
        UniqueConstraint("carrier_id", "tracking_no", name="uq_freight_shipment_tracking"),
    )

    carrier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("freight_carriers.id", ondelete="RESTRICT"),
        nullable=False, index=True)
    # Normalised: spaces/hyphens removed, upper case (crud.freight.normalize_tracking).
    tracking_no: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # outbound | inbound
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="outbound",
                                           server_default="outbound")
    # The waybill carries no date; filled from the invoice once billed.
    ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cost_center_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_centers.id", ondelete="RESTRICT"),
        nullable=False, index=True)
    # Derived from the cost centre at write time; kept so lists need no join.
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # File Server key of the waybill photo, when one was uploaded.
    attachment_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # registered (at ship time) | from_invoice (added while matching an invoice)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="registered",
                                        server_default="registered")
    # pending (not billed yet) | billed | void
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending",
                                        server_default="pending", index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class FreightInvoiceLine(UUIDPrimaryKey, TimestampMixin, Base):
    """One charge on a carrier invoice's detail pages."""
    __tablename__ = "freight_invoice_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(nullable=False, default=0)
    # shipment | pickup | fee | adjustment
    charge_type: Mapped[str] = mapped_column(String(20), nullable=False, default="shipment")
    # Lead tracking number; NULL for pickups and account-level fees.
    tracking_no: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Every package number of a multi-piece shipment, lead included — a buyer
    # may have registered any of them (UPS 1Z…551028 + 1Z…544637).
    package_tracking_nos: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Reference / pickup request number as printed (UPS 1st ref, FedEx Ref#2, pickup no.).
    reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ship_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Pre-tax, fuel/discounts/surcharges included; negative for credits.
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    # Tax attributed to this line (D13).
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"),
                                                server_default="0")
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("freight_shipments.id", ondelete="SET NULL"),
        nullable=True, index=True)
    # A pickup charge may follow a shipment line on the same invoice (and its cost centre).
    linked_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("freight_invoice_lines.id", ondelete="SET NULL"),
        nullable=True)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_centers.id", ondelete="RESTRICT"), nullable=True)
    # unmatched | auto | manual | duplicate_billing | unregistered | default
    match_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unmatched",
                                              server_default="unmatched")


class PaCostAllocation(UUIDPrimaryKey, Base):
    """Cost-centre split of a Freight PA, snapshotted when the PA is raised."""
    __tablename__ = "pa_cost_allocations"

    pa_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_applications.id", ondelete="CASCADE"),
        nullable=False, index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="RESTRICT"),
        nullable=False, index=True)
    cost_center_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_centers.id", ondelete="RESTRICT"), nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False)
    budget_code: Mapped[str] = mapped_column(String(20), nullable=False, default=FREIGHT_BUDGET_CODE)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
