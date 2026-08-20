from datetime import datetime
from decimal import Decimal
from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class ErpMaterial(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "erp_materials"

    erp_part_no: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_meas: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dim_quality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    weight_net: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    weight_gross: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    part_status: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    item_mes_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # ERP `exp` = shelf-life in months; `part_PRODUCT_FAMILY` = product family.
    # Both already ride along inside raw_payload (the full ERP response row);
    # these columns just surface them without a JSONB lookup on every sync.
    exp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    part_product_family: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # ERP material classification, the same tree NC calls 物料基本分类
    # (BD_MARBASCLASS): 0101 Raw Milk, 0102 Raw Ingredient, 02 Packaging,
    # 03 Standardized Milk, 04 Storage Silo Powder, 05 Finished Products,
    # 06 Chemical, 07 Mechanical, 08 Laboratory, 98 Fee, 99 Test.
    # Present on every payload (2,573/2,573) and promoted to
    # materials.erp_class_code by material_sync. MRP excludes 0101 from every
    # stock and on-order figure.
    accounting_group: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    accounting_group_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    erp_rowversion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
