"""Read-write mirror of EPMS purchase_orders — workflow execution only."""
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PurchaseOrder(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "purchase_orders"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Second, independent workflow over the same row: the sign-off of an
    # NC-imported PO. doc_type "posign" points the engine's status_attr /
    # step_attr here so it never touches the two columns above (nor they it —
    # nc_purchase_sync rewrites `status` on every sync).
    signoff_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="draft")
    signoff_step_idx: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0")
    signoff_submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True)
    total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_prepaid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pr_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    place_order_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
