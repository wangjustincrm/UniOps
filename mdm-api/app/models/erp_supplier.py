from datetime import datetime
from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class ErpSupplier(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "erp_suppliers"

    erp_supplier_code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    supplier_name: Mapped[str] = mapped_column(String(255), nullable=False)
    supplier_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_tel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    supplier_fax: Mapped[str | None] = mapped_column(String(50), nullable=True)
    supplier_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    erp_rowversion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
