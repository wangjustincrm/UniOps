"""material_suppliers — supplier-side supply parameters for a material,
hand-maintained (MRP phase0 task 6).

`material_code` / `partner_code` are plain String(50) references (no FK) into
`materials.code` / `business_partners.code` — same loose-coupling convention
as bom_lines.component_material_code and boms.product_material_code (see
app/models/bom.py, app/models/material.py). Phase 1 purchase-suggestion logic
picks the default supplier + lead time for a material via `is_primary`.
"""
from sqlalchemy import Boolean, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class MaterialSupplier(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "material_suppliers"
    __table_args__ = (
        UniqueConstraint("material_code", "partner_code", name="uq_material_suppliers_material_partner"),
    )

    material_code: Mapped[str] = mapped_column(String(50), index=True)
    partner_code: Mapped[str] = mapped_column(String(50))
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    moq: Mapped[object | None] = mapped_column(Numeric(18, 4))
    order_multiple: Mapped[object | None] = mapped_column(Numeric(18, 4))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    price_ref: Mapped[object | None] = mapped_column(Numeric(18, 4))
    notes: Mapped[str | None] = mapped_column(Text)
