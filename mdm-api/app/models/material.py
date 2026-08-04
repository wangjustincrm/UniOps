from sqlalchemy import String, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class Material(Base, UUIDPrimaryKey, TimestampMixin):
    """Formal material master, promoted from the erp_material mirror.

    `code` (== erp_material.erp_part_no) is the global material key that
    later MRP-phase0 tasks join on. ERP-sourced fields are refreshed by
    material_sync.sync_materials(); locally-governed fields (item_type,
    procurement_type, shelf_life_months, product_family, factory_code) are
    never overwritten by sync — erp_material carries no source data for
    shelf_life_months/product_family today, so those stay manually curated.
    """

    __tablename__ = "materials"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    spec: Mapped[str | None] = mapped_column(String(255))          # ERP dim_QUALITY
    item_type: Mapped[str | None] = mapped_column(String(20))      # raw/aux/packaging/semi/finished，可后补
    erp_item_type: Mapped[str | None] = mapped_column(String(20))  # ERP 原始 itemMESType，保底追溯
    base_uom: Mapped[str | None] = mapped_column(String(20))
    shelf_life_months: Mapped[int | None] = mapped_column(Integer)  # 本地治理，erp_material 无源数据
    procurement_type: Mapped[str] = mapped_column(String(20), default="purchase")  # purchase/manufacture
    product_family: Mapped[str | None] = mapped_column(String(100))  # 本地治理，erp_material 无源数据
    factory_code: Mapped[str | None] = mapped_column(String(50))
    erp_id: Mapped[str | None] = mapped_column(String(50), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
