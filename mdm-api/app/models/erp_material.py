from datetime import datetime
from decimal import Decimal
from sqlalchemy import Boolean, DateTime, Numeric, String, Text
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
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    erp_rowversion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
