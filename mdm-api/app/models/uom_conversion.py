"""Unit-of-measure conversion factors, mirrored from NC ERP unitTranf.

Phase 1's BOM/MRP engine reads `rate` to convert quantities across units:
qty_in_to_uom = qty_in_from_uom * rate.
"""
from sqlalchemy import Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class UomConversion(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "uom_conversions"
    __table_args__ = (UniqueConstraint("from_uom", "to_uom", name="uq_uom_conv"),)

    from_uom: Mapped[str] = mapped_column(String(20), index=True)
    to_uom: Mapped[str] = mapped_column(String(20), index=True)
    rate: Mapped[object] = mapped_column(Numeric(18, 8))
