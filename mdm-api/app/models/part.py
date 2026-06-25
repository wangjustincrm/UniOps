from sqlalchemy import Boolean, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from decimal import Decimal
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class Part(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "parts"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier: Mapped[str] = mapped_column(String(255), nullable=False)
    supplier_part_no: Mapped[str] = mapped_column(String(100), nullable=False)
    supplier_item_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=Decimal("0"), nullable=False)
    unit: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
