"""The chemical inventory and its safety data sheets.

WHMIS 2015 (Ontario Reg 860) is the reason this exists, and it sets the shape.
An employer must keep an inventory of the hazardous products at the workplace,
must hold a current SDS for each, must make that SDS available to workers, and
must not let an SDS go more than three years without review.

Two tables rather than one, because a chemical and its data sheet have
different lifetimes. The drum of caustic in the CIP room is the same drum when
the supplier issues a revised sheet; the sheet is superseded, the chemical is
not. Folding them together would mean either losing the old sheet or
duplicating the chemical every time a supplier revises one.

The thing this is built to surface is not the inventory. It is the two gaps:
a hazardous product with no SDS at all, and an SDS past its three-year review.
Both are findings an inspector writes up, and neither is visible in a list
sorted by name.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Numeric, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Chemical(UUIDPrimaryKey, TimestampMixin, Base):
    """One hazardous product, and where it is kept."""

    __tablename__ = "ehs_chemicals"

    chemical_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(250), nullable=False, index=True)
    # What the supplier calls it, when that differs from what the plant does.
    product_name: Mapped[str | None] = mapped_column(String(250), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    supplier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    product_code: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # GHS hazard classes, as a list of {id, label} taken from the vocabulary.
    # A list because a product is routinely several — corrosive and toxic — and
    # a single field would force whoever entered it to pick the worst and drop
    # the rest.
    hazard_classes: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]")
    # Danger | Warning | None — GHS allows only these, and it is what goes on
    # the workplace label.
    signal_word: Mapped[str | None] = mapped_column(String(10), nullable=True)
    physical_state: Mapped[str | None] = mapped_column(String(20), nullable=True)

    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                          nullable=True, index=True)
    location_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_note: Mapped[str | None] = mapped_column(String(300), nullable=True)

    container_size: Mapped[str | None] = mapped_column(String(60), nullable=True)
    quantity_on_site: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)

    ppe_required: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_aid_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Retired rather than deleted: an incident three years ago may reference a
    # product no longer on site, and the record has to keep meaning something.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                            default=True, server_default="true", index=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SafetyDataSheet(UUIDPrimaryKey, TimestampMixin, Base):
    """One revision of one product's data sheet.

    `is_current` rather than a date comparison: a supplier's revision date is
    what they printed on it, which is not always later than the one it
    replaces, and "which sheet do I hand a worker" must never depend on
    parsing that correctly.
    """

    __tablename__ = "ehs_sds"

    chemical_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_chemicals.id", ondelete="CASCADE"),
        nullable=False, index=True)
    revision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # The date the supplier put on the sheet. Reg 860's three years run from
    # here, not from when it was filed.
    revision_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    review_due_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False,
                                          default="en", server_default="en")

    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                             default=True, server_default="true", index=True)
    # Printed on the container label and on the shelf edge, so a phone reaches
    # the current sheet without anyone navigating to it.
    qr_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
