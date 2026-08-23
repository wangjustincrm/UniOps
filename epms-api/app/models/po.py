"""ORM models for Purchase Order (PO) and its line items."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PurchaseOrder(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "purchase_orders"

    number: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # 1=Raw Materials, 2=Consumables, 3=Spare Parts, 4=Service, 5=Fixed Assets, 6=Software
    type: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft", index=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")

    subtotal: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0"))
    # mdm-api tax_codes.code snapshot — which code produced tax_rate (NULL = legacy)
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=False, index=True
    )
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_prepaid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    budget_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expected_delivery: Mapped[date | None] = mapped_column(Date, nullable=True)
    delivery_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Buyer-supplied detail, filled in by hand after an NC import. NC owns
    # `notes` (it rewrites it every sync with its own memo plus [NC Paid] /
    # [NC Closed] markers), so buyer text needs a column of its own.
    # nc_purchase_sync/writer.py must never add these to its UPDATE lists.
    buyer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    incoterms: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Set only when a buyer-initiated edit changes tax_rate (see crud.po.
    # update_imported_details). nc_purchase_sync/writer.py reads this as "the
    # tax rate was set by hand" and, while it is non-null, keeps the stored
    # tax_rate instead of NC's incoming value, re-deriving tax_amount/total
    # from NC's fresh subtotal. Editing unrelated fields (Incoterms, a line's
    # Supplier Item ID, ...) must NOT stamp this column.
    buyer_edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # NC ERP provenance (NULL for non-NC POs)
    source: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    nc_source_pk: Mapped[str | None] = mapped_column(String(50), nullable=True)

    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Source PR (optional — PO can be created without a PR)
    pr_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_requests.id", ondelete="RESTRICT"),
        nullable=True, index=True
    )
    pr_number: Mapped[str | None] = mapped_column(String(30), nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Place order tracking (set when Procurement Officer places the order)
    place_order_method: Mapped[str | None] = mapped_column(String(10), nullable=True)   # "email" | "online"
    place_order_email_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    place_order_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    line_items: Mapped[list["PoLineItem"]] = relationship(
        "PoLineItem", back_populates="po", cascade="all, delete-orphan", lazy="selectin",
        order_by="PoLineItem.sort_order"
    )


class PoLineItem(UUIDPrimaryKey, Base):
    __tablename__ = "po_line_items"

    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    pr_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pr_line_items.id", ondelete="SET NULL"),
        nullable=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    material_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    supplier_item_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Free-text sample requirement asked of the vendor, e.g. "500 g" / "2 ea".
    sample: Mapped[str | None] = mapped_column(String(100), nullable=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    received_qty: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False, default=Decimal("0"))
    # The ERP's own planned arrival date for THIS line
    # (NCSC.PO_ORDER_B.DPLANARRVDATE), brought across by the NC purchase sync.
    #
    # Line level, not header: NC lets each line carry its own date and real
    # orders do (PO-009-2603-01's two lines differ), which is why this is not
    # the header's `expected_delivery`. NULL for UniOps-native POs, where the
    # hand-entered header date is the only date there is — so readers fall
    # back to the header and treat "neither" as unknown rather than as today.
    planned_arrival_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    nc_source_pk: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    po: Mapped["PurchaseOrder"] = relationship("PurchaseOrder", back_populates="line_items")
