from sqlalchemy import String, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class Material(Base, UUIDPrimaryKey, TimestampMixin):
    """Formal material master, promoted from the erp_material mirror.

    `code` (== erp_material.erp_part_no) is the global material key that
    later MRP-phase0 tasks join on. ERP-sourced fields (including
    shelf_life_months <- erp_material.exp and product_family <-
    erp_material.part_product_family, added in Task 3) are refreshed by
    material_sync.sync_materials(); locally-governed fields (item_type,
    procurement_type, factory_code) are never overwritten by sync.
    """

    __tablename__ = "materials"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    spec: Mapped[str | None] = mapped_column(String(255))          # ERP dim_QUALITY
    item_type: Mapped[str | None] = mapped_column(String(20))      # raw/aux/packaging/semi/finished，可后补
    erp_item_type: Mapped[str | None] = mapped_column(String(20))  # ERP 原始 itemMESType，保底追溯
    base_uom: Mapped[str | None] = mapped_column(String(20))
    shelf_life_months: Mapped[int | None] = mapped_column(Integer)  # ERP exp
    procurement_type: Mapped[str] = mapped_column(String(20), default="purchase")  # purchase/manufacture
    product_family: Mapped[str | None] = mapped_column(String(100))  # ERP part_PRODUCT_FAMILY
    # ERP/NC material classification (accounting_group / 物料基本分类).
    # '0101' = Raw Milk, which MRP excludes from stock and on-order figures --
    # raw milk is delivered by tanker straight into production, never
    # warehoused as lots, and its NC receipts do not reconcile against PO
    # quantities. Classify by THIS, never by code prefix: CR0059 "Pasteurized
    # Milk" is 0101 while carrying an ordinary raw-material prefix.
    erp_class_code: Mapped[str | None] = mapped_column(String(20), index=True)
    erp_class_name: Mapped[str | None] = mapped_column(String(100))
    factory_code: Mapped[str | None] = mapped_column(String(50))
    erp_id: Mapped[str | None] = mapped_column(String(50), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
